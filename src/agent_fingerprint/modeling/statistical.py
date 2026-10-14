"""Native-missing-value XGBoost baseline on the fixed v5 feature schema."""

import numpy as np
from xgboost import XGBClassifier
from xgboost.callback import TrainingCallback

from agent_fingerprint.features.feature_schema import FEATURE_NAMES, SCHEMA
from agent_fingerprint.storage.io import write_json

from .preprocessing import StatisticalDataset
from .progress import report_progress


def fit_statistical(sessions, indices, classes, config, destination):
    loss_metric = "mlogloss" if len(classes) > 2 else "logloss"
    error_metric = "merror" if len(classes) > 2 else "error"

    class ProgressCallback(TrainingCallback):
        def after_iteration(self, model, epoch, evals_log):
            # Keep validation_0 as validation for existing training-log consumers.
            train, val = evals_log["validation_1"], evals_log["validation_0"]
            report_progress(
                f"[XGBoost] Round {epoch + 1}/{config.xgb_estimators} "
                f"train_loss={train[loss_metric][-1]:.4f} "
                f"train_acc={1 - train[error_metric][-1]:.4f} "
                f"val_loss={val[loss_metric][-1]:.4f} "
                f"val_acc={1 - val[error_metric][-1]:.4f}"
            )
            return False

    X = StatisticalDataset(sessions).values
    y = np.array(
        [
            classes.index(getattr(s, config.target))
            if getattr(s, config.target) in classes
            else -1
            for s in sessions
        ]
    )
    estimator = XGBClassifier(
        n_estimators=config.xgb_estimators,
        max_depth=config.xgb_max_depth,
        learning_rate=config.xgb_learning_rate,
        random_state=config.seed,
        n_jobs=config.threads,
        tree_method="hist",
        missing=np.nan,
        eval_metric=[loss_metric, error_metric],
        callbacks=[ProgressCallback()],
    )
    weights = None
    if config.class_weight == "balanced":
        counts = np.bincount(y[indices["train"]], minlength=len(classes))
        weights = len(indices["train"]) / (len(classes) * counts[y[indices["train"]]])
    estimator.fit(
        X[indices["train"]],
        y[indices["train"]],
        sample_weight=weights,
        eval_set=[
            (X[indices["val"]], y[indices["val"]]),
            (X[indices["train"]], y[indices["train"]]),
        ],
        verbose=False,
    )
    estimator.save_model(destination / "checkpoint.ubj")
    write_json(
        destination / "feature_importance.json",
        {
            "schema": SCHEMA,
            "importance_type": estimator.importance_type or "gain",
            "features": dict(
                zip(FEATURE_NAMES, map(float, estimator.feature_importances_))
            ),
            "interpretation": "Predictive importance; not a disentanglement result",
        },
    )
    write_json(destination / "training_log.json", estimator.evals_result())
    predictions = {p: estimator.predict(X[ids]).tolist() for p, ids in indices.items()}
    return predictions, {
        "feature_names": list(FEATURE_NAMES),
        "feature_schema": SCHEMA,
        "best_epoch": None,
        "timing_normalization": None,
        "sequence_config": None,
    }
