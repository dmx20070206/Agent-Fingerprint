"""Train statistical and semantic classifiers for agent and LLM labels."""
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

from agent_fingerprint.analysis.dataset import write_json
from agent_fingerprint.analysis.selection import add_selection_arguments, selections, site_label, matches, validate_selection
from .evaluate import evaluate
from .representations import specification, matrix
from .plot_results import plot_evaluation


def make_estimator(seed=42, regularization_c=1.0):
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)),
        ('scaler', StandardScaler()),
        ('classifier', LogisticRegression(C=regularization_c, max_iter=5000, random_state=seed)),
    ])


def train(dataset_path, output_dir, target='agent', folds=5, seed=42, regularization_c=1.0, *, sites=None, agents=None, llms=None, representation='statistics'):
    schema, names, _ = specification(representation)
    selection = selections(sites, agents, llms)
    if target not in {'agent', 'llm'}:
        raise ValueError('target must be agent or llm')
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f'Output directory must be empty: {destination}')
    source_bytes = Path(dataset_path).read_bytes()
    data = json.loads(source_bytes)
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
    X = matrix(data, representation)
    if any(not isinstance(r.get(target + '_label'), str) or not r[target + '_label'].strip() for r in rows):
        raise ValueError('Missing target label')
    y = np.array([r[target + '_label'] for r in rows])
    estimator = make_estimator(seed, regularization_c)
    report = evaluate(estimator, X, y, ids, folds, seed)
    destination.mkdir(parents=True, exist_ok=True)
    config = {'dataset': str(Path(dataset_path).resolve()), 'dataset_sha256': hashlib.sha256(source_bytes).hexdigest(),
              'selection': selection, 'dataset_selection': data.get('selection'),
              'target': target, 'folds_requested': folds, 'seed': seed, 'regularization_c': regularization_c,
              'schema': schema, 'feature_names': list(names), 'representation': representation,
              'versions': {'python': platform.python_version(), 'sklearn': sklearn.__version__, 'numpy': np.__version__}}
    write_json(destination / 'config.json', config)
    # Exact input snapshot makes provenance independent of future dataset changes.
    write_json(destination / 'samples.json', data)
    write_json(destination / 'evaluation.json', report)
    if report['status'] == 'completed':
        estimator.fit(X, y)
        joblib.dump({'pipeline': estimator, 'schema': schema, 'feature_names': list(names), 'representation': representation,
                     'target': target, 'classes': estimator.classes_.tolist()}, destination / 'model.joblib')
    plot_evaluation(report, destination, target)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('configs/classification.yaml'))
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--target', choices=['agent', 'llm', 'both'], default='both')
    parser.add_argument('--representation', choices=['statistics', 'semantic', 'both'], default='both')
    add_selection_arguments(parser)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding='utf-8')) or {}
    targets = ['agent', 'llm'] if args.target == 'both' else [args.target]
    representations = ['statistics', 'semantic'] if args.representation == 'both' else [args.representation]
    reports = {}
    for representation in representations:
        for target in targets:
            output = args.output_dir
            if args.representation == 'both':
                output = output / representation
            if args.target == 'both':
                output = output / target
            reports[f'{representation}/{target}'] = train(
                args.dataset or config['dataset'], output, target,
                config.get('folds', 5), config.get('seed', 42), config.get('regularization_c', 1.0),
                sites=args.sites, agents=args.agents, llms=args.llms, representation=representation)
    print(json.dumps({key: {k: r[k] for k in ('status', 'reason', 'metrics') if k in r}
                      for key, r in reports.items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
