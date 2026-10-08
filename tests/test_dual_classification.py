import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_fingerprint.analysis.dataset import build_dataset
from agent_fingerprint.analysis.predict import predict
from agent_fingerprint.analysis.representations import SEMANTIC_NAMES, semantic_features
from agent_fingerprint.analysis.test_models import test_model as evaluate_saved_model
from agent_fingerprint.storage.io import write_json


def make_runs(root, prefix):
    for i in range(8):
        run_id = f'{prefix}-{i}'
        run = root / run_id
        (run / 'fingerprints').mkdir(parents=True)
        write_json(run / 'manifest.json', {
            'run_id': run_id, 'agent': {'name': f'agent-{i % 2}', 'model': f'llm-{i % 2}'},
            'status': 'success'})
        write_json(run / 'fingerprints/l3_browser_dynamic.json', {
            'run_id': run_id, 'enabled': True, 'events': [
                {'type': 'click', 'monotonic_ms': 10 + j * (i + 1), 'session_id': run_id,
                 'target': {'tag': 'BUTTON'}} for j in range(i + 1)]})


def test_semantic_projection_ignores_metadata_and_preserves_order():
    base = {'action_sequence_content': [['CLICK', 'BUTTON'], ['SCROLL', 'PAGE']],
            'action_sequence_temporal': [['CLICK', 'BUTTON', 10, None], ['SCROLL', 'PAGE', 30, 20]]}
    features = semantic_features(base)
    assert tuple(features) == SEMANTIC_NAMES
    assert features['transition:CLICK:SCROLL'] == 1
    assert features['duration_ms:mean'] == 20
    assert features['inter_action_latency_ms:observed'] == 1
    assert features == semantic_features({**base, 'raw_trace': {'agent': 'secret'}, 'audit': 'secret'})
    assert semantic_features({'action_sequence_content': [], 'action_sequence_temporal': []})['duration_ms:mean'] is None


def test_four_models_train_predict_and_independent_test(tmp_path):
    make_runs(tmp_path / 'raw_train', 'train')
    make_runs(tmp_path / 'raw_test', 'test')
    for split in ('train', 'test'):
        assert build_dataset(tmp_path / f'raw_{split}', tmp_path / split)['included'] == 8
    repo = Path(__file__).resolve().parents[1]
    models = tmp_path / 'models'
    result = subprocess.run([sys.executable, '-m', 'agent_fingerprint', 'train',
                             '--dataset', str(tmp_path / 'train/samples.json'),
                             '--output-dir', str(models)], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / 'train/samples.json').read_text())
    assert data['provenance']['semantic_parser'] == 'semantic-actions/v1'
    assert 'semantic_features' in data['samples'][0]
    for representation in ('statistics', 'semantic'):
        for target in ('agent', 'llm'):
            model = models / representation / target / 'model.joblib'
            assert model.exists()
            prediction = predict(model, tmp_path / 'raw_test/test-0/fingerprints/l3_browser_dynamic.json')
            assert prediction['representation'] == representation
            assert prediction['target'] == target
            assert sum(prediction['probabilities'].values()) == pytest.approx(1)
            with pytest.raises(ValueError, match='overlap'):
                evaluate_saved_model(model, tmp_path / 'train/samples.json', tmp_path / 'overlap.json')
    result = subprocess.run([sys.executable, '-m', 'agent_fingerprint', 'test',
                             '--dataset', str(tmp_path / 'test/samples.json'),
                             '--model-dir', str(models), '--output-dir', str(tmp_path / 'reports')],
                            cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / 'reports').glob('*/*/evaluation.json'))) == 4
    for target in ('agent', 'llm'):
        a = json.loads((models / 'statistics' / target / 'evaluation.json').read_text())
        b = json.loads((models / 'semantic' / target / 'evaluation.json').read_text())
        assert a['splits'] == b['splits']


def test_shell_dispatches_four_models_for_training_and_testing(tmp_path):
    import os
    fake = tmp_path / 'python'
    log = tmp_path / 'calls.jsonl'
    fake.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                    'with open(os.environ["CALL_LOG"], "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")\n')
    fake.chmod(0o755)
    env = {**os.environ, 'PYTHON': str(fake), 'CALL_LOG': str(log)}
    repo = Path(__file__).resolve().parents[1]
    for command in ('train', 'test'):
        log.write_text('')
        args = ['bash', 'scripts/run_analysis.sh', command, '--run-name', 'dual-test']
        if command == 'test':
            args += ['--test-dataset', '/independent/samples.json']
        result = subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(calls) == 4
        assert {(call[call.index('--target') + 1], call[call.index('--representation') + 1])
                for call in calls} == {(t, r) for t in ('agent', 'llm') for r in ('statistics', 'semantic')}
        for call in calls:
            target = call[call.index('--target') + 1]
            representation = call[call.index('--representation') + 1]
            flag = '--output-dir' if command == 'train' else '--model-dir'
            assert call[call.index(flag) + 1] == f'data/experiments/l3_dual-test_{target}/{representation}'
