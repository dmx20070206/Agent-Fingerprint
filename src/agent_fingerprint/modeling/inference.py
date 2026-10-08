"""Re-evaluate persisted models on their frozen split, without fitting anything."""

from pathlib import Path

from agent_fingerprint.storage.io import read_json, write_json

from .evaluation import export_evaluation
from .preprocessing import StatisticalDataset
from .splits import validate_split
from .training import TrainingConfig, load_checkpoint, make_loaders, predict_loader


def evaluate_saved_experiment(sessions, experiment_dir, output_dir, *, device="cpu"):
    source, destination = Path(experiment_dir), Path(output_dir)
    config = read_json(source / "config.json")
    manifest = read_json(source / "split_manifest.json")
    if (
        config.get("status") != "completed"
        or config.get("schema") != "attribution-experiment/v1"
    ):
        raise ValueError("Expected a completed attribution experiment")
    if config["split_id"] != manifest["split_id"]:
        raise ValueError("Experiment/split mismatch")
    indices = validate_split(sessions, manifest, config["target"])
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Evaluation output directory must be empty")
    classes = sorted(config["class_mapping"], key=config["class_mapping"].get)
    if config["representation"] == "stat98":
        from xgboost import XGBClassifier

        model = XGBClassifier()
        model.load_model(source / "checkpoint.ubj")
        X = StatisticalDataset(sessions).values
        predictions = {p: model.predict(X[ids]).tolist() for p, ids in indices.items()}
    else:
        model, preprocessor, checkpoint_classes, target = load_checkpoint(
            source / "checkpoint.pt", device
        )
        if (
            classes != checkpoint_classes
            or target != config["target"]
            or preprocessor.to_dict() != config["sequence_config"]
        ):
            raise ValueError("Checkpoint/config mismatch")
        training_config = TrainingConfig(
            representation=config["representation"],
            target=target,
            semantic_mode=config["semantic_mode"],
            batch_size=config["batch_size"],
        )
        _, loaders = make_loaders(
            sessions, indices, preprocessor, training_config, classes
        )
        predictions = {
            p: predict_loader(model, loader, device)[0] for p, loader in loaders.items()
        }
    destination.mkdir(parents=True, exist_ok=True)
    write_json(
        destination / "evaluation_source.json",
        {
            "experiment": str(source.resolve()),
            "split_id": manifest["split_id"],
            "refitted": False,
        },
    )
    return export_evaluation(
        destination, sessions, indices, config["target"], classes, predictions
    )
