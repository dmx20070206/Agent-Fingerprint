"""Shared training entry point: choose agent or llm without changing the features."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

import joblib
import numpy as np
import sklearn
import yaml
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .dataset import write_json
from .selection import add_selection_arguments, selections, site_label, matches, validate_selection
from .evaluate import evaluate
from .plot_results import plot_evaluation
from .feature_schema import SCHEMA, FEATURE_NAMES


def make_estimator(seed=42, regularization_c=1.0):
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)),
        ('scaler', StandardScaler()),
        ('classifier', LogisticRegression(C=regularization_c, max_iter=5000, random_state=seed)),
    ])


def train(dataset_path, output_dir, target='agent', folds=5, seed=42, regularization_c=1.0, *, sites=None, agents=None, llms=None):
    selection = selections(sites, agents, llms)
    if target not in {'agent', 'llm'}:
        raise ValueError('target must be agent or llm')
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f'Output directory must be empty: {destination}')
    source_bytes = Path(dataset_path).read_bytes()
    data = json.loads(source_bytes)
    if data.get('schema') != SCHEMA or data.get('feature_names') != list(FEATURE_NAMES):
        raise ValueError('Feature schema/order mismatch; rebuild the dataset')
    rows = data['samples']
    if any(selection.values()):
        available = {key: set() for key in selection}
        selected = []
        for row in rows:
            site = row.get('site_label')
            if site is None and row.get('source_path'):
                site = site_label(Path(row['source_path']).parent.parent,
                                  row.get('agent_label'), row.get('llm_label'))
            labels = {'sites': site, 'agents': row.get('agent_label'), 'llms': row.get('llm_label')}
            for key, value in labels.items():
                if isinstance(value, str) and value:
                    available[key].add(value)
            if matches(labels, selection):
                selected.append(row)
        validate_selection(selection, available)
        if not selected:
            raise ValueError('No samples match the selected sites/agents/llms')
        rows = selected
        data = {**data, 'samples': rows}

    ids = [r['sample_id'] for r in rows]
    if len(set(ids)) != len(ids) or len({r['run_id'] for r in rows}) != len(rows):
        raise ValueError('Expected one unique sample per run')
    if any(set(r['features']) != set(FEATURE_NAMES) for r in rows):
        raise ValueError('Sample feature names mismatch')
    if any(not isinstance(r.get(target + '_label'), str) or not r[target + '_label'].strip() for r in rows):
        raise ValueError('Missing target label')
    X = np.array([[np.nan if r['features'][n] is None else r['features'][n] for n in FEATURE_NAMES] for r in rows], dtype=float).reshape(len(rows), len(FEATURE_NAMES))
    if np.isinf(X).any() or any(isinstance(v, float) and np.isnan(v) for r in rows for v in r['features'].values()):
        raise ValueError('Use JSON null for missing values; non-finite numbers are invalid')
    y = np.array([r[target + '_label'] for r in rows])
    estimator = make_estimator(seed, regularization_c)
    report = evaluate(estimator, X, y, ids, folds, seed)
    destination.mkdir(parents=True, exist_ok=True)
    config = {'dataset': str(Path(dataset_path).resolve()), 'dataset_sha256': hashlib.sha256(source_bytes).hexdigest(),
              'selection': selection, 'dataset_selection': data.get('selection'),
              'target': target, 'folds_requested': folds, 'seed': seed, 'regularization_c': regularization_c,
              'schema': SCHEMA, 'feature_names': list(FEATURE_NAMES),
              'versions': {'python': platform.python_version(), 'sklearn': sklearn.__version__, 'numpy': np.__version__}}
    write_json(destination / 'config.json', config)
    # Exact input snapshot makes provenance independent of future dataset changes.
    write_json(destination / 'samples.json', data)
    write_json(destination / 'evaluation.json', report)
    if report['status'] == 'completed':
        estimator.fit(X, y)
        joblib.dump({'pipeline': estimator, 'schema': SCHEMA, 'feature_names': list(FEATURE_NAMES),
                     'target': target, 'classes': estimator.classes_.tolist()}, destination / 'model.joblib')
    plot_evaluation(report, destination, target)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config/classification.yaml'))
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--target', choices=['agent', 'llm'], required=True)
    add_selection_arguments(parser)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding='utf-8')) or {}
    report = train(args.dataset or config['dataset'], args.output_dir, args.target,
                   config.get('folds', 5), config.get('seed', 42), config.get('regularization_c', 1.0),
                   sites=args.sites, agents=args.agents, llms=args.llms)
    print(json.dumps({k: report[k] for k in ('status', 'reason', 'metrics') if k in report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
