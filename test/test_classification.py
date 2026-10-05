import json

import joblib
import numpy as np
import pytest

from analysis.dataset import build_dataset, write_json
from analysis.feature_schema import FEATURE_NAMES, SCHEMA
from analysis.normalize_run_ids import normalize
from analysis.train import train


def make_run(root, name, agent='a', model='m', empty=False):
    directory = root / name
    (directory / 'fingerprints').mkdir(parents=True)
    write_json(directory / 'manifest.json', {'run_id': name, 'agent': {'name': agent, 'model': model}, 'status': 'success', 'task': {'prompt': 'test'}})
    write_json(directory / 'fingerprints/l3_browser_dynamic.json', {
        'run_id': name, 'enabled': True, 'events': [] if empty else [
            {'type': 'keydown', 'key': 'a', 'monotonic_ms': 0, 'session_id': name},
            {'type': 'keyup', 'key': 'a', 'monotonic_ms': 10, 'session_id': name}]})
    return directory


def test_dataset_quality_and_id_repair(tmp_path):
    root = tmp_path / 'raw'
    run = make_run(root, 'valid')
    make_run(root, 'empty', empty=True)
    write_json(run / 'manifest.json', {'run_id': 'wrong', 'agent': {'name': 'a', 'model': 'm'}})
    assert normalize(root)  # Preview must not change files.
    assert json.loads((run / 'manifest.json').read_text())['run_id'] == 'wrong'
    assert normalize(root, apply=True)
    assert normalize(root, apply=True) == []
    report = build_dataset(root, tmp_path / 'dataset')
    assert report['included'] == 1
    assert report['excluded'][0]['sample_id'] == 'empty'
    data = json.loads((tmp_path / 'dataset/samples.json').read_text())
    assert list(data['samples'][0]['features']) == list(FEATURE_NAMES)
    assert data['samples'][0]['features']['mouse_direction_mean'] is None
    with pytest.raises(ValueError, match='empty'):
        build_dataset(root, tmp_path / 'dataset')


def synthetic_dataset(path):
    rows = []
    for i in range(12):
        features = dict.fromkeys(FEATURE_NAMES, None)
        features['input_count'] = i % 2 * 100 + i
        features['hold_latency_mean'] = None if i == 0 else i
        rows.append({'sample_id': str(i), 'run_id': str(i), 'agent_label': 'a' if i % 2 else 'b',
                     'llm_label': 'only-model', 'features': features})
    write_json(path, {'schema': SCHEMA, 'feature_names': list(FEATURE_NAMES), 'samples': rows})
    return rows


def test_training_oof_persistence_and_single_class_skip(tmp_path):
    source = tmp_path / 'samples.json'
    rows = synthetic_dataset(source)
    report = train(source, tmp_path / 'agent', folds=3)
    assert report['status'] == 'completed'
    assert 'baseline_metrics' not in report
    assert all('baseline' not in row for row in report['predictions'])
    assert (tmp_path / 'agent/evaluation.png').read_bytes().startswith(b'\x89PNG')
    assert '<svg' in (tmp_path / 'agent/evaluation.svg').read_text()
    tested = []
    for split in report['splits']:
        assert not set(split['train']) & set(split['test'])
        tested.extend(split['test'])
    assert sorted(tested) == sorted(r['sample_id'] for r in rows)
    bundle = joblib.load(tmp_path / 'agent/model.joblib')
    X = np.array([[np.nan if r['features'][n] is None else r['features'][n] for n in FEATURE_NAMES] for r in rows])
    assert len(bundle['pipeline'].predict(X)) == len(rows)
    assert bundle['pipeline'].n_features_in_ == 98
    skip = train(source, tmp_path / 'llm', target='llm')
    assert skip['status'] == 'skipped'
    assert not (tmp_path / 'llm/model.joblib').exists()
    assert not (tmp_path / 'llm/evaluation.png').exists()
    data = json.loads(source.read_text())
    data['feature_names'].reverse()
    write_json(source, data)
    with pytest.raises(ValueError, match='schema'):
        train(source, tmp_path / 'invalid')


def test_preprocessing_fits_only_training_folds(tmp_path, monkeypatch):
    from sklearn.impute import SimpleImputer
    observed = []
    original = SimpleImputer.fit

    def record(self, X, y=None):
        observed.append(len(X))
        return original(self, X, y)

    monkeypatch.setattr(SimpleImputer, 'fit', record)
    source = tmp_path / 'samples.json'
    synthetic_dataset(source)
    train(source, tmp_path / 'experiment', folds=3)
    assert observed == [8, 8, 8, 12]  # Three training folds, then final fit.


def test_llm_multiclass_and_raw_prediction(tmp_path):
    from analysis.predict import predict
    source = tmp_path / 'samples.json'
    synthetic_dataset(source)
    data = json.loads(source.read_text())
    for i, row in enumerate(data['samples']):
        row['llm_label'] = 'model-a' if i % 2 else 'model-b'
    write_json(source, data)
    report = train(source, tmp_path / 'llm', target='llm', folds=3)
    assert report['status'] == 'completed'
    run = make_run(tmp_path / 'raw', 'new')
    result = predict(tmp_path / 'llm/model.joblib', run / 'fingerprints/l3_browser_dynamic.json')
    assert result['target'] == 'llm'
    assert result['predicted'] in {'model-a', 'model-b'}
    assert sum(result['probabilities'].values()) == pytest.approx(1)


def test_insufficient_support_and_empty_dataset_skip(tmp_path):
    from analysis.evaluate import evaluate
    from analysis.train import make_estimator
    for y in [np.array([]), np.array(['a', 'b', 'b'])]:
        X = np.zeros((len(y), 98))
        result = evaluate(make_estimator(), X, y, [str(i) for i in range(len(y))])
        assert result['status'] == 'skipped'


def test_dataset_excludes_duplicate_traces_and_missing_labels(tmp_path):
    root = tmp_path / 'raw'
    first = make_run(root, 'first')
    duplicate = make_run(root, 'duplicate')
    raw = json.loads((first / 'fingerprints/l3_browser_dynamic.json').read_text())
    raw['run_id'] = 'duplicate'
    write_json(duplicate / 'fingerprints/l3_browser_dynamic.json', raw)
    make_run(root, 'missing-label', model='')
    report = build_dataset(root, tmp_path / 'dataset')
    assert report['included'] == 1
    assert len(report['excluded']) == 2


def test_dataset_collection_agent_llm_selection(tmp_path):
    root = tmp_path / 'raw'
    for name, site, agent, model in [('one', 'flights', 'a', 'm'),
                                     ('two', 'shop', 'a', 'm'),
                                     ('three', 'flights', 'b', 'm'),
                                     ('four', 'flights', 'a', 'n')]:
        make_run(root / site / agent / model, name, agent, model)
    assert build_dataset(root, tmp_path / 'all')['included'] == 4
    report = build_dataset(root, tmp_path / 'selected', sites='flights', agents='a', llms='m,n')
    assert report['included'] == 2
    assert len(report['filtered_out']) == 2
    assert report['excluded'] == []
    assert report['site_counts'] == {'flights': 2}
    data = json.loads((tmp_path / 'selected/samples.json').read_text())
    assert {r['run_id'] for r in data['samples']} == {'one', 'four'}
    assert data['selection'] == {'sites': ['flights'], 'agents': ['a'], 'llms': ['m', 'n']}
    assert build_dataset(root / 'flights', tmp_path / 'subroot', sites='flights')['included'] == 3
    with pytest.raises(ValueError, match='Unknown sites'):
        build_dataset(root, tmp_path / 'bad', sites='typo')
    with pytest.raises(ValueError, match='No usable runs'):
        build_dataset(root, tmp_path / 'none', sites='shop', agents='b')
    assert not (tmp_path / 'none').exists()


def test_training_selection_snapshot_and_legacy_site_inference(tmp_path):
    source = tmp_path / 'samples.json'
    rows = synthetic_dataset(source)
    data = json.loads(source.read_text())
    for i, row in enumerate(data['samples']):
        # Older v4 datasets have a source path but no site_label.
        site = 'flights' if i < 8 else 'shop'
        row['source_path'] = f'/raw/{site}/{row["agent_label"]}/only-model/{i}/fingerprints/l3_browser_dynamic.json'
    write_json(source, data)
    report = train(source, tmp_path / 'selected', sites='flights', agents='a,b', llms='only-model', folds=2)
    assert report['status'] == 'completed'
    snapshot = json.loads((tmp_path / 'selected/samples.json').read_text())
    assert len(snapshot['samples']) == 8
    assert len(json.loads(source.read_text())['samples']) == len(rows)
    config = json.loads((tmp_path / 'selected/config.json').read_text())
    assert config['selection']['sites'] == ['flights']
    with pytest.raises(ValueError, match='Unknown llms'):
        train(source, tmp_path / 'invalid-model', llms='typo')


def test_selection_all_and_invalid_syntax():
    from analysis.selection import selections
    assert selections('all', 'all', 'all') == selections()
    assert selections(agents=' a,b,a ')['agents'] == ['a', 'b']
    for value in ('', ',', 'all,a'):
        with pytest.raises(ValueError):
            selections(agents=value)


def test_shell_forwards_filters_and_rejects_regression_test_selection(tmp_path):
    import os
    import subprocess
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    fake = tmp_path / 'python'
    args_file = tmp_path / 'args.json'
    fake.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                    'open(os.environ["ARGS_FILE"], "w").write(json.dumps(sys.argv[1:]))\n')
    fake.chmod(0o755)
    env = {**os.environ, 'PYTHON': str(fake), 'ARGS_FILE': str(args_file)}
    for command in ('dataset', 'train'):
        result = subprocess.run(['bash', 'scripts/run_analysis.sh', command,
                                 '--sites', 'flights', '--agents', 'a,b', '--llms', 'm'],
                                cwd=repo, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        args = json.loads(args_file.read_text())
        for flag, value in [('--sites', 'flights'), ('--agents', 'a,b'), ('--llms', 'm')]:
            assert args[args.index(flag) + 1] == value
    result = subprocess.run(['bash', 'scripts/run_analysis.sh', 'test', '--sites', 'flights'],
                            cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'regression tests' in result.stderr
