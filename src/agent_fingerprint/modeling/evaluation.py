"""Consistent metrics, predictions and train-only sanity baselines."""

import csv
from collections import Counter

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from agent_fingerprint.analysis.evaluate import metrics as existing_metrics
from agent_fingerprint.semantic_actions.schema import ACTION_TYPES
from agent_fingerprint.storage.io import write_json


def classification_metrics(truth, predicted, classes):
    report = existing_metrics(truth, predicted, classes)
    macro = report["per_class"]["macro avg"]
    report.update(
        {
            "macro_precision": float(macro["precision"]),
            "macro_recall": float(macro["recall"]),
            "weighted_f1": float(report["per_class"]["weighted avg"]["f1-score"]),
        }
    )
    report["per_class"] = {name: report["per_class"][name] for name in classes}
    return report


def class_counts(sessions, indices, target, classes):
    return {
        partition: {
            name: sum(getattr(sessions[i], target) == name for i in ids)
            for name in classes
        }
        for partition, ids in indices.items()
    }


def sanity_baselines(sessions, indices, target, classes, seed):
    labels = np.array([getattr(s, target) for s in sessions])
    train, test = indices["train"], indices["test"]
    counts = Counter(labels[train])
    majority = min(counts, key=lambda name: (-counts[name], name))
    result = {
        "majority": classification_metrics(
            labels[test], [majority] * len(test), classes
        )
    }
    actions = sorted(ACTION_TYPES)
    histograms = []
    for session in sessions:
        counts = Counter(t.kind for t in session.semantic)
        histograms.append(
            [counts[k] for k in actions]
            + [counts[k] / max(1, len(session.semantic)) for k in actions]
        )
    features = {
        "event_length": [[len(s.events)] for s in sessions],
        "semantic_length": [[len(s.semantic)] for s in sessions],
        "action_histogram": histograms,
    }
    for name, values in features.items():
        X = np.asarray(values, dtype=float)
        estimator = make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=2000, random_state=seed)
        )
        estimator.fit(X[train], labels[train])
        result[name] = classification_metrics(
            labels[test], estimator.predict(X[test]), classes
        )
    return result


def export_evaluation(destination, sessions, indices, target, classes, predictions):
    reports, rows = {}, []
    for partition, ids in indices.items():
        truth = [getattr(sessions[i], target) for i in ids]
        predicted = [classes[int(i)] for i in predictions[partition]]
        reports[partition] = classification_metrics(truth, predicted, classes)
        rows.extend(
            {
                "session_id": sessions[i].session_id,
                "split": partition,
                "ground_truth": actual,
                "prediction": prediction,
            }
            for i, actual, prediction in zip(ids, truth, predicted)
        )
    write_json(destination / "metrics.json", reports)
    with (destination / "predictions.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["session_id", "split", "ground_truth", "prediction"]
        )
        writer.writeheader()
        writer.writerows(rows)
    with (destination / "confusion_matrix.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ground_truth / prediction", *classes])
        for name, row in zip(classes, reports["test"]["confusion_matrix"]):
            writer.writerow([name, *row])
    return reports
