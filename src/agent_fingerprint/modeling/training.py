"""Shared single-task training, best-validation checkpointing and evaluation."""

from __future__ import annotations

import os
import platform
import random
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from agent_fingerprint.storage.io import write_json

from .data import dataset_fingerprint
from .evaluation import (
    class_counts,
    classification_metrics,
    export_evaluation,
    sanity_baselines,
)
from .models import TransformerConfig, build_transformer
from .preprocessing import (
    EventSequenceDataset,
    SemanticActionDataset,
    SequencePreprocessor,
    collate_sequences,
)
from .progress import report_progress
from .splits import validate_split


@dataclass(frozen=True)
class TrainingConfig:
    representation: str = "stat98"
    target: str = "agent"
    semantic_mode: str = "content"
    seed: int = 42
    batch_size: int = 16
    epochs: int = 50
    early_stopping_patience: int = 20
    lr: float = 1e-4
    weight_decay: float = 1e-2
    class_weight: str = "none"
    max_seq_len: int | None = None
    use_page_boundary: bool | None = None
    device: str = "cpu"
    threads: int = 2
    xgb_estimators: int = 200
    xgb_max_depth: int = 4
    xgb_learning_rate: float = 0.05

    def __post_init__(self):
        if self.representation not in {"stat98", "event", "semantic"}:
            raise ValueError("Invalid representation")
        if self.target not in {"agent", "llm"} or self.semantic_mode not in {
            "content",
            "timing",
        }:
            raise ValueError("Invalid target/semantic mode")
        if self.class_weight not in {"none", "balanced"}:
            raise ValueError("class_weight must be none or balanced")
        if (
            min(
                self.batch_size,
                self.epochs,
                self.early_stopping_patience,
                self.threads,
                self.xgb_estimators,
            )
            < 1
        ):
            raise ValueError("Batch size, epochs, patience, threads and estimators must be positive")
        if self.lr <= 0 or self.weight_decay < 0:
            raise ValueError("Invalid learning rate/weight decay")


def seed_everything(seed, threads=2):
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="CUDA initialization: The NVIDIA driver on your system is too old",
            category=UserWarning,
            module=r"torch\.cuda",
        )
        cuda_available = torch.cuda.is_available()
    if cuda_available:
        torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(threads)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True)


def model_inputs(batch, device):
    return {key: batch[key].to(device) for key in ("token_ids", "role_ids", "timing", "padding_mask")}


def predict_loader(model, loader, device):
    model.eval()
    predictions, labels = [], []
    with torch.no_grad():
        for batch in loader:
            predictions.extend(model(**model_inputs(batch, device)).argmax(dim=-1).cpu().tolist())
            labels.extend(batch["labels"].tolist())
    return predictions, labels


def make_loaders(sessions, indices, preprocessor, config, classes):
    dataset_class = EventSequenceDataset if config.representation == "event" else SemanticActionDataset
    datasets = {
        p: dataset_class([sessions[i] for i in ids], preprocessor, config.target, classes) for p, ids in indices.items()
    }
    loaders = {
        p: DataLoader(d, batch_size=config.batch_size, collate_fn=collate_sequences) for p, d in datasets.items()
    }
    generator = torch.Generator().manual_seed(config.seed)
    training_loader = DataLoader(
        datasets["train"],
        batch_size=config.batch_size,
        shuffle=True,
        collate_fn=collate_sequences,
        generator=generator,
    )
    return training_loader, loaders


def fit_transformer(model, training_loader, validation_loader, config, classes, *, log_path=None):
    """Select only by validation macro-F1; return the restored best model."""
    device = torch.device(config.device)
    model.to(device)
    counts = np.bincount([s["label"] for s in training_loader.dataset], minlength=len(classes))
    weights = None
    if config.class_weight == "balanced":
        weights = torch.tensor(counts.sum() / (len(classes) * counts), dtype=torch.float32, device=device)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    best_score, best_epoch, stale, best_state = -1.0, 0, 0, None
    history = []
    for epoch in range(1, config.epochs + 1):
        model.train()
        total_loss, count, correct = 0.0, 0, 0
        for batch in training_loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(**model_inputs(batch, device))
            labels = batch["labels"].to(device)
            loss = criterion(logits, labels)
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite training loss; inspect input normalization")
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(batch["labels"])
            count += len(batch["labels"])
            correct += (logits.detach().argmax(dim=-1) == labels).sum().item()
        predicted, truth = predict_loader(model, validation_loader, device)
        validation_metrics = classification_metrics(
            [classes[i] for i in truth], [classes[i] for i in predicted], classes
        )
        score = validation_metrics["macro_f1"]
        entry = {
            "epoch": epoch,
            "train_loss": total_loss / count,
            "train_accuracy": correct / count,
            "val_accuracy": validation_metrics["accuracy"],
            "val_macro_f1": score,
        }
        history.append(entry)
        if log_path is not None:
            # Preserve completed epochs even if the process is interrupted.
            write_json(log_path, history)
        if score > best_score:
            best_score, best_epoch, stale = score, epoch, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
        report_progress(
            f"[Transformer] Epoch {epoch}/{config.epochs} "
            f"train_loss={entry['train_loss']:.4f} "
            f"train_acc={entry['train_accuracy']:.4f} "
            f"val_acc={entry['val_accuracy']:.4f} val_macro_f1={score:.4f} "
            f"best_epoch={best_epoch} patience={stale}/{config.early_stopping_patience}"
        )
        if stale >= config.early_stopping_patience:
            report_progress(f"[Transformer] Early stopping at epoch {epoch}")
            break
    model.load_state_dict(best_state)
    report_progress(f"[Transformer] Restored best epoch {best_epoch} " f"(val_macro_f1={best_score:.4f})")
    return history, best_epoch


def save_checkpoint(path, model, preprocessor, model_config, classes, target):
    torch.save(
        {
            "schema": "attribution-transformer/v1",
            "state_dict": model.state_dict(),
            "preprocessor": preprocessor.to_dict(),
            "model_config": model_config.to_dict(),
            "classes": classes,
            "target": target,
        },
        path,
    )


def load_checkpoint(path, device="cpu"):
    checkpoint = torch.load(path, map_location=device, weights_only=True)
    if checkpoint.get("schema") != "attribution-transformer/v1":
        raise ValueError("Unsupported checkpoint schema")
    preprocessor = SequencePreprocessor.from_dict(checkpoint["preprocessor"])
    model = build_transformer(
        preprocessor,
        len(checkpoint["classes"]),
        TransformerConfig(**checkpoint["model_config"]),
    )
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device).eval()
    return model, preprocessor, checkpoint["classes"], checkpoint["target"]


def train_experiment(sessions, manifest, output_dir, config=None, model_config=None):
    config = config or TrainingConfig()
    model_config = model_config or TransformerConfig()
    indices = validate_split(sessions, manifest, config.target)
    classes = sorted({getattr(sessions[i], config.target) for i in indices["train"]})
    if len(classes) < 2:
        raise ValueError("Classification requires at least two training classes")
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Output directory must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    report_progress(
        f"[train] representation={config.representation} "
        f"semantic_mode={config.semantic_mode} target={config.target} seed={config.seed} "
        f"device={'cpu' if config.representation == 'stat98' else config.device} "
        + " ".join(f"{p}={len(ids)}" for p, ids in indices.items())
    )
    seed_everything(config.seed, config.threads)
    import sklearn

    experiment = {
        **asdict(config),
        "model": (
            model_config.to_dict()
            if config.representation != "stat98"
            else {
                "name": "XGBClassifier",
                "n_estimators": config.xgb_estimators,
                "max_depth": config.xgb_max_depth,
                "learning_rate": config.xgb_learning_rate,
                "missing": "NaN (native XGBoost handling)",
            }
        ),
        "status": "running",
        "schema": "attribution-experiment/v1",
        "split_mode": manifest["mode"],
        "split_id": manifest["split_id"],
        "dataset_fingerprint": dataset_fingerprint(sessions),
        "class_mapping": {name: i for i, name in enumerate(classes)},
        "class_counts": class_counts(sessions, indices, config.target, classes),
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "torch": str(torch.__version__),
            "sklearn": sklearn.__version__,
        },
    }
    write_json(destination / "config.json", experiment)
    write_json(destination / "split_manifest.json", manifest)
    write_json(
        destination / "baselines.json",
        sanity_baselines(sessions, indices, config.target, classes, config.seed),
    )
    if config.representation == "stat98":
        from xgboost import __version__ as xgb_version

        from .statistical import fit_statistical

        predictions, metadata = fit_statistical(sessions, indices, classes, config, destination)
        experiment.update(metadata)
        experiment["versions"]["xgboost"] = xgb_version
    else:
        training = [sessions[i] for i in indices["train"]]
        preprocessor = SequencePreprocessor.fit(
            training,
            representation=config.representation,
            semantic_mode=config.semantic_mode,
            max_seq_len=config.max_seq_len,
            use_page_boundary=config.use_page_boundary,
        )
        training_loader, loaders = make_loaders(sessions, indices, preprocessor, config, classes)
        model = build_transformer(preprocessor, len(classes), model_config)
        experiment.update(
            {
                "sequence_config": preprocessor.to_dict(),
                "timing_normalization": preprocessor.normalizer.to_dict(),
                "sequence_statistics": {
                    p: preprocessor.report([sessions[i] for i in ids]) for p, ids in indices.items()
                },
            }
        )
        write_json(destination / "config.json", experiment)
        history, best_epoch = fit_transformer(
            model,
            training_loader,
            loaders["val"],
            config,
            classes,
            log_path=destination / "training_log.json",
        )
        save_checkpoint(
            destination / "checkpoint.pt",
            model,
            preprocessor,
            model_config,
            classes,
            config.target,
        )
        predictions = {p: predict_loader(model, loader, config.device)[0] for p, loader in loaders.items()}
        experiment.update({"best_epoch": best_epoch, "epochs_completed": len(history)})
    metrics = export_evaluation(destination, sessions, indices, config.target, classes, predictions)
    experiment["status"] = "completed"
    write_json(destination / "config.json", experiment)
    report_progress(
        f"[train] Completed: test_acc={metrics['test']['accuracy']:.4f} "
        f"test_macro_f1={metrics['test']['macro_f1']:.4f} output={destination}"
    )
    return metrics
