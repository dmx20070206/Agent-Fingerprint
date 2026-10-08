"""Run-level stratified evaluation with fold-local preprocessing and OOF predictions."""
from collections import Counter

import numpy as np
from sklearn.base import clone
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedKFold


def metrics(y, predicted, labels):
    return {'accuracy': float(accuracy_score(y, predicted)),
            'macro_f1': float(f1_score(y, predicted, labels=labels, average='macro', zero_division=0)),
            'labels': list(labels), 'confusion_matrix': confusion_matrix(y, predicted, labels=labels).tolist(),
            'per_class': classification_report(y, predicted, labels=labels, output_dict=True, zero_division=0)}


def evaluate(estimator, X, y, sample_ids, requested_folds=5, seed=42):
    counts = Counter(y)
    if len(counts) < 2:
        return {'status': 'skipped', 'reason': 'At least two label classes are required', 'class_counts': dict(counts)}
    if min(counts.values()) < 2:
        return {'status': 'skipped', 'reason': 'Each class needs at least two runs for cross-validation', 'class_counts': dict(counts)}
    if requested_folds < 2:
        raise ValueError('folds must be at least 2')
    folds = min(requested_folds, min(counts.values()))
    labels = sorted(counts)
    predictions = np.empty(len(y), dtype=object)
    splits, fold_scores = [], []
    for fold, (train, test) in enumerate(StratifiedKFold(folds, shuffle=True, random_state=seed).split(X, y)):
        fitted = clone(estimator).fit(X[train], y[train])
        predicted = fitted.predict(X[test])
        predictions[test] = predicted
        splits.append({'fold': fold, 'train': [sample_ids[i] for i in train], 'test': [sample_ids[i] for i in test]})
        fold_scores.append(metrics(y[test], predicted, labels))
    return {'status': 'completed', 'folds': folds, 'class_counts': dict(counts),
            'metrics': metrics(y, predictions, labels),
            'fold_metrics': fold_scores, 'splits': splits,
            'predictions': [{'sample_id': sid, 'true': truth, 'predicted': pred}
                            for sid, truth, pred in zip(sample_ids, y, predictions)]}
