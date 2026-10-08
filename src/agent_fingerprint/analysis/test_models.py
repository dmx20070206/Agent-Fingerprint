"""Evaluate saved classifiers on independent labeled runs without fitting."""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from .evaluate import metrics
from .representations import matrix, specification
from agent_fingerprint.storage.io import write_json


def test_model(model_path, dataset_path, output_path):
    model_path, output_path = Path(model_path), Path(output_path)
    if output_path.exists():
        raise ValueError(f'Output already exists: {output_path}')
    bundle = joblib.load(model_path)
    representation = bundle.get('representation', 'statistics')
    schema, names, _ = specification(representation)
    if bundle['schema'] != schema or bundle['feature_names'] != list(names):
        raise ValueError('Model feature schema mismatch')
    data = json.loads(Path(dataset_path).read_text())
    training = json.loads((model_path.parent / 'samples.json').read_text())
    rows = data['samples']
    if not rows:
        raise ValueError('Test dataset is empty')
    for key in ('run_id', 'raw_sha256', 'events_sha256'):
        known = {r[key] for r in training['samples'] if r.get(key)}
        if any(r.get(key) in known for r in rows):
            raise ValueError(f'Train/test overlap detected by {key}; use independent runs')
    target = bundle['target']
    y = [r.get(target + '_label') for r in rows]
    if any(not isinstance(label, str) or not label.strip() for label in y):
        raise ValueError('Missing target label')
    X = matrix(data, representation)
    predicted = bundle['pipeline'].predict(X)
    labels = sorted(set(bundle['classes']) | set(y))
    report = {'status': 'completed', 'target': target, 'representation': representation,
              'model': str(model_path.resolve()), 'dataset': str(Path(dataset_path).resolve()),
              'metrics': metrics(np.array(y), predicted, labels),
              'unseen_labels': sorted(set(y) - set(bundle['classes'])),
              'predictions': [{'sample_id': r['sample_id'], 'true': truth, 'predicted': str(pred)}
                              for r, truth, pred in zip(rows, y, predicted)]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--target', choices=['agent', 'llm', 'both'], default='both')
    parser.add_argument('--representation', choices=['statistics', 'semantic', 'both'], default='both')
    args = parser.parse_args()
    reports = {}
    for representation in (['statistics', 'semantic'] if args.representation == 'both' else [args.representation]):
        for target in (['agent', 'llm'] if args.target == 'both' else [args.target]):
            path = args.model_dir
            if args.representation == 'both':
                path /= representation
            if args.target == 'both':
                path /= target
            report = test_model(path / 'model.joblib', args.dataset,
                                args.output_dir / representation / target / 'evaluation.json')
            reports[f'{representation}/{target}'] = report['metrics']
    print(json.dumps(reports, ensure_ascii=False, indent=2))
