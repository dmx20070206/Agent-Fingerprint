import json
from dataclasses import replace

import pytest

from agent_fingerprint.modeling.aggregate import aggregate
from agent_fingerprint.modeling.models import TransformerConfig
from agent_fingerprint.modeling.splits import make_split
from agent_fingerprint.modeling.training import TrainingConfig, train_experiment


def test_all_eight_paths_export_comparable_results_and_tables(sessions, tmp_path):
    manifest = make_split(sessions)
    base = TrainingConfig(epochs=2, batch_size=32, xgb_estimators=3)
    architecture = TransformerConfig(d_model=16, n_heads=2, num_layers=1, ffn_dim=32)
    paths = []
    for representation, mode in [
        ("stat98", "content"),
        ("event", "content"),
        ("semantic", "content"),
        ("semantic", "timing"),
    ]:
        for target in ("agent", "llm"):
            destination = tmp_path / f"{representation}_{mode}_{target}"
            metrics = train_experiment(
                sessions,
                manifest,
                destination,
                replace(
                    base,
                    representation=representation,
                    semantic_mode=mode,
                    target=target,
                ),
                architecture,
            )
            paths.append(destination)
            assert 0 <= metrics["test"]["macro_f1"] <= 1
            from agent_fingerprint.modeling.inference import evaluate_saved_experiment

            reloaded = evaluate_saved_experiment(
                sessions, destination, destination / "reevaluated"
            )
            assert reloaded == metrics
            for name in (
                "accuracy",
                "macro_precision",
                "macro_recall",
                "weighted_f1",
                "per_class",
                "confusion_matrix",
            ):
                assert name in metrics["test"]
            config = json.loads((destination / "config.json").read_text())
            assert config["status"] == "completed"
            assert config["split_id"] == manifest["split_id"]
            assert (
                json.loads((destination / "split_manifest.json").read_text())
                == manifest
            )
            assert (
                (destination / "predictions.csv")
                .read_text()
                .startswith("session_id,split,ground_truth,prediction")
            )
            baselines = json.loads((destination / "baselines.json").read_text())
            assert set(baselines) == {
                "majority",
                "event_length",
                "semantic_length",
                "action_histogram",
            }
            if representation == "stat98":
                assert (
                    len(
                        json.loads(
                            (destination / "feature_importance.json").read_text()
                        )["features"]
                    )
                    == 98
                )
            else:
                assert config["best_epoch"] >= 1
    summary = aggregate(tmp_path, tmp_path / "summary")
    assert len(summary) == 8
    markdown = (tmp_path / "summary/tables.md").read_text()
    assert "Timing ablation" in markdown
    assert "Component generalization" not in markdown
    assert "random / split=" in markdown
    assert all(r in markdown for r in ("98D", "E", "Z_content", "Z_timing"))
    with pytest.raises(ValueError, match="empty"):
        train_experiment(sessions, manifest, paths[0], base, architecture)


def test_multiseed_summary_and_duplicate_protection(sessions, tmp_path):
    manifest = make_split(sessions)
    config = TrainingConfig(xgb_estimators=2)
    for seed in [1, 2]:
        train_experiment(
            sessions, manifest, tmp_path / str(seed), replace(config, seed=seed)
        )
    summary = aggregate(tmp_path, tmp_path / "summary")
    assert summary[0]["n"] == 2
    import shutil

    shutil.copytree(tmp_path / "1", tmp_path / "duplicate")
    with pytest.raises(ValueError, match="Duplicate seed"):
        aggregate(tmp_path, tmp_path / "bad_summary")


def test_cli_full_cycle(dataset_file, tmp_path):
    import subprocess
    import sys

    split = tmp_path / "split.json"
    output = tmp_path / "model"
    prefix = [sys.executable, "-m", "agent_fingerprint", "attribution"]
    commands = [
        ["split", "--dataset", str(dataset_file), "--output", str(split)],
        [
            "train",
            "--dataset",
            str(dataset_file),
            "--split-manifest",
            str(split),
            "--representation",
            "semantic",
            "--semantic-mode",
            "timing",
            "--target",
            "llm",
            "--output-dir",
            str(output),
            "--epochs",
            "1",
            "--d-model",
            "16",
            "--n-heads",
            "2",
            "--num-layers",
            "1",
            "--ffn-dim",
            "32",
        ],
        [
            "inspect",
            "--dataset",
            str(dataset_file),
            "--checkpoint",
            str(output / "checkpoint.pt"),
            "--session-id",
            "a0-m0-r0",
            "--session-id",
            "a0-m0-r1",
            "--output",
            str(tmp_path / "inspect.json"),
        ],
        [
            "aggregate",
            "--input-dir",
            str(output),
            "--output-dir",
            str(tmp_path / "summary"),
        ],
    ]
    for arguments in commands:
        result = subprocess.run(
            prefix + arguments, capture_output=True, text=True, timeout=45, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr
    inspection = json.loads((tmp_path / "inspect.json").read_text())
    assert len(inspection["batch"]["padding_mask"]) == 2


def test_best_checkpoint_uses_macro_f1_not_accuracy(sessions, monkeypatch):
    import torch

    from agent_fingerprint.modeling import training
    from agent_fingerprint.modeling.models import build_transformer
    from agent_fingerprint.modeling.preprocessing import SequencePreprocessor

    pp = SequencePreprocessor.fit(sessions[:5], representation="event")
    config = TrainingConfig(representation="event", epochs=3, batch_size=5)
    classes = ["a0", "a1"]
    loader, _ = training.make_loaders(
        sessions[:5], {"train": list(range(5))}, pp, config, classes
    )
    model = build_transformer(
        pp, 2, TransformerConfig(d_model=8, n_heads=2, num_layers=1, ffn_dim=16)
    )
    predicted = iter([[0, 0, 0, 0, 0], [0, 0, 1, 1, 1], [1, 1, 1, 1, 1]])
    states = []

    def validation(model, loader, device):
        states.append({k: v.detach().clone() for k, v in model.state_dict().items()})
        return next(predicted), [0, 0, 0, 0, 1]

    monkeypatch.setattr(training, "predict_loader", validation)
    history, best = training.fit_transformer(model, loader, loader, config, classes)
    assert best == 2
    assert history[1]["val_macro_f1"] > history[0]["val_macro_f1"]
    assert all(
        torch.equal(model.state_dict()[key], value) for key, value in states[1].items()
    )
