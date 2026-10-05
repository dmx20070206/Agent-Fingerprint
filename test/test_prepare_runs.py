import json
import subprocess
from pathlib import Path

import pytest

from analysis._common import resolve_run_dir
from analysis.dataset import build_dataset
from analysis.prepare_runs import prepare


def make_run(root, name, agent='browseruse', model='gemini'):
    run = root / name
    (run / 'fingerprints').mkdir(parents=True)
    (run / 'manifest.json').write_text(json.dumps({
        'run_id': name, 'agent': {'name': agent, 'model': model},
        'task': {'task_id': 'shared-task'}, 'status': 'success'}))
    (run / 'fingerprints/l3_browser_dynamic.json').write_text(json.dumps({
        'run_id': 'stale', 'events': [{'type': 'keydown', 'key': 'a', 'session_id': name}]}))
    (run / 'events.jsonl').write_text(json.dumps({'nested': {'run_id': name}, 'session_id': name}) + '\n')
    (run / 'capture.bin').write_bytes(b'unchanged\x00')
    return run


def test_prepare_preview_unique_ids_normalization_and_dataset(tmp_path):
    root = tmp_path / 'final'
    first = make_run(root / 'shop/browseruse/gemini', 'browseruse_gemini_shop_1')
    second = make_run(root, 'autogen_gemini_shop_1', agent='autogen')
    (second / 'artifacts/framework/tasksingle').mkdir(parents=True)
    empty = root / 'flights/autogen/gemini/autogen_gemini_flights_1'
    empty.mkdir(parents=True)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    preview = prepare(root)
    assert len(preview['renamed']) == 2
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    report = prepare(root, apply=True)
    assert report['renamed'] == preview['renamed']
    assert not first.exists() and not second.exists()
    assert not empty.exists()
    for row in report['renamed']:
        run = root / row['destination']
        assert run.name == row['run_id']
        manifest = json.loads((run / 'manifest.json').read_text())
        assert manifest['run_id'] == run.name
        assert manifest['task']['task_id'] == 'shared-task'
        raw = json.loads((run / 'fingerprints/l3_browser_dynamic.json').read_text())
        assert raw['run_id'] == run.name
        assert raw['events'][0]['session_id'] == row['old_run_id']
        events = json.loads((run / 'events.jsonl').read_text())
        assert events['nested']['run_id'] == run.name
        assert events['session_id'] == row['old_run_id']
        assert (run / 'capture.bin').read_bytes() == b'unchanged\x00'
        assert resolve_run_dir(run.name, root) == run
    assert build_dataset(root, tmp_path / 'dataset')['included'] == 2
    state = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    again = prepare(root, apply=True)
    assert again['renamed'] == again['normalized_files'] == []
    assert again['index_updates'] == 0
    assert state == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_incremental_ids_and_external_index(tmp_path):
    root = tmp_path / 'final'
    index = tmp_path / 'index.jsonl'
    history = json.dumps({'run_id': 'historical', 'path': 'original'}) + '\n'
    index.write_text(history)
    make_run(root, 'browseruse_gemini_shop_1')
    first = prepare(root, apply=True, index=index)['renamed'][0]
    make_run(root, 'browseruse_gemini_flights_1')
    second = prepare(root, apply=True, index=index)['renamed'][0]
    assert first['run_id'] == 'run_0001'
    assert second['run_id'] == 'run_0002'
    assert index.read_text().startswith(history)
    assert resolve_run_dir('run_0002', tmp_path) == root / second['destination']
    assert len(json.loads((root / 'run_names.json').read_text())) == 2


@pytest.mark.parametrize('problem', ['labels', 'duplicate', 'destination', 'index'])
def test_preflight_failure_leaves_runs_untouched(tmp_path, problem):
    root = tmp_path / 'final'
    first = make_run(root, 'browseruse_gemini_shop_1')
    if problem == 'labels':
        make_run(root / 'shop/wrong/gemini', 'browseruse_gemini_shop_2')
    elif problem == 'duplicate':
        make_run(root / 'shop/browseruse/gemini', 'run_0001')
        make_run(root / 'flights/browseruse/gemini', 'run_0001')
    elif problem == 'destination':
        (root / 'shop/browseruse/gemini/run_0001').mkdir(parents=True)
    else:
        (root / 'index.jsonl').write_text('invalid json\n')
    with pytest.raises((ValueError, FileExistsError)):
        prepare(root, apply=True)
    assert first.exists()
    assert json.loads((first / 'manifest.json').read_text())['run_id'] == first.name
    assert not (root / 'run_names.json').exists()


def test_bash_prepare_from_another_directory_and_argument_errors(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/run_analysis.sh'
    root = tmp_path / 'runs with spaces'
    run = make_run(root, 'browseruse_gemini_shop_1')
    args = ['bash', str(script), 'prepare', '--input-dir', str(root),
            '--report', str(tmp_path / 'audit.json')]
    preview = subprocess.run(args + ['--dry-run'], cwd=tmp_path, capture_output=True, text=True)
    assert preview.returncode == 0, preview.stderr
    assert run.exists()
    applied = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert not run.exists()
    for options in [['--input-dir'], ['--target', 'invalid'], ['--dry-run']]:
        result = subprocess.run(['bash', str(script), *options], capture_output=True, text=True)
        assert result.returncode == 2
        assert 'Error:' in result.stderr
