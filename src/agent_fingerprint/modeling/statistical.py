"""Native-missing-value XGBoost baseline on the fixed v5 feature schema."""

import numpy as np
from xgboost import XGBClassifier

from agent_fingerprint.features.feature_schema import FEATURE_NAMES, SCHEMA
from agent_fingerprint.storage.io import write_json

from .preprocessing import StatisticalDataset


def fit_statistical(sessions, indices, classes, config, destination):
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
        eval_metric="mlogloss" if len(classes) > 2 else "logloss",
    )
    weights = None
    if config.class_weight == "balanced":
        counts = np.bincount(y[indices["train"]], minlength=len(classes))
        weights = len(indices["train"]) / (len(classes) * counts[y[indices["train"]]])
    estimator.fit(
        X[indices["train"]],
        y[indices["train"]],
        sample_weight=weights,
        eval_set=[(X[indices["val"]], y[indices["val"]])],
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
