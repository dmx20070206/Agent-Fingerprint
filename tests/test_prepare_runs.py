import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_fingerprint.analysis._common import resolve_run_dir
from agent_fingerprint.analysis.dataset import build_dataset
from agent_fingerprint.storage.prepare_runs import prepare as _prepare


def prepare(*args, **kwargs):
    return _prepare(*args, legacy_short_ids=True, **kwargs)


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
        assert resolve_run_dir(row['destination'], root) == run
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
    assert second['run_id'] == 'run_0001'
    assert index.read_text().startswith(history)
    assert resolve_run_dir('final/' + second['destination'], tmp_path) == root / second['destination']
    assert len(json.loads((root / 'run_names.json').read_text())) == 2


@pytest.mark.parametrize('problem', ['labels', 'duplicate', 'destination', 'index'])
def test_preflight_failure_leaves_runs_untouched(tmp_path, problem):
    root = tmp_path / 'final'
    first = make_run(root, 'browseruse_gemini_shop_1')
    if problem == 'labels':
        make_run(root / 'shop/wrong/gemini', 'browseruse_gemini_shop_2')
    elif problem == 'duplicate':
        make_run(root / 'shop/browseruse/gemini', 'run_0001')
        duplicate = make_run(root / 'incoming', 'run_0001')
        path = duplicate / 'manifest.json'
        data = json.loads(path.read_text())
        data['task']['site'] = 'shop'
        path.write_text(json.dumps(data))
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
    environment = os.environ.copy()
    environment['PYTHON'] = sys.executable
    environment['PYTHONPATH'] = os.pathsep.join(
        part for part in (str(script.parents[1] / 'src'), environment.get('PYTHONPATH', '')) if part
    )
    preview = subprocess.run(args + ['--dry-run'], cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert preview.returncode == 0, preview.stderr
    assert run.exists()
    applied = subprocess.run(args, cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert not run.exists()
    for options in [['--input-dir'], ['--target', 'invalid'], ['--dry-run']]:
        result = subprocess.run(['bash', str(script), *options], capture_output=True, text=True)
        assert result.returncode == 2
        assert 'Error:' in result.stderr


def test_metadata_classification_of_dated_runs_and_nested_artifacts(tmp_path):
    root = tmp_path / 'final'
    run = make_run(root / '2026-10-11', 'agente_qwen3_5_9b_1011_1905',
                   agent='agente', model='qwen3.5-9b')
    path = run / 'manifest.json'
    metadata = json.loads(path.read_text())
    metadata['task']['site'] = 'shop'
    path.write_text(json.dumps(metadata))
    nested = run / 'artifacts/framework/manifest.json'
    nested.parent.mkdir(parents=True)
    nested.write_text('{"run_id": "framework-original"}')
    before = nested.read_bytes()
    preview = prepare(root)
    assert preview['runs_found'] == 1
    assert run.exists()
    assert preview['renamed'][0]['destination'] == 'shop/agente/qwen3.5-9b/run_0001'
    prepare(root, apply=True)
    target = root / preview['renamed'][0]['destination']
    assert (target / 'artifacts/framework/manifest.json').read_bytes() == before
    assert json.loads((target / 'manifest.json').read_text())['run_id'] == 'run_0001'
    assert prepare(root)['renamed'] == []


def test_unknown_site_rejected_before_mutation(tmp_path):
    root = tmp_path / 'final'
    run = make_run(root, 'unclassified')
    with pytest.raises(ValueError, match='task.site'):
        prepare(root, apply=True)
    assert run.exists()
    assert not (root / 'run_names.json').exists()


def test_short_ids_cli_alias(tmp_path):
    root = tmp_path / 'final'
    run = make_run(root, 'browseruse_gemini_shop_1')
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    result = subprocess.run([sys.executable, '-m', 'agent_fingerprint', 'prepare',
                             '--input-dir', str(root), '--short-ids'],
                            capture_output=True, text=True, env=environment)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['renamed'][0]['run_id'] == 'run_0001'
    assert run.exists()


def test_per_group_renumbering_lookup_dataset_and_stable_prepare(tmp_path):
    from agent_fingerprint.storage.prepare import prepare_stable
    from agent_fingerprint.modeling.data import load_sessions
    from agent_fingerprint.analysis._common import paths
    root = tmp_path / 'final'
    for group, old_id in [('shop/browseruse/gemini', 'run_0002'),
                          ('shop/browseruse/gemini', 'run_0010'),
                          ('flights/browseruse/gemini', 'run_0003'),
                          ('shop/agente/gemini', 'run_0004')]:
        make_run(root / group, old_id, agent=group.split('/')[1])
    preview = prepare(root)
    assert len(preview['renamed']) == 4
    prepare(root, apply=True)
    assert (root / 'shop/browseruse/gemini/run_0002').is_dir()
    assert len(list(root.rglob('run_0001'))) == 3
    index = [json.loads(line) for line in (root / 'index.jsonl').read_text().splitlines()]
    assert len(index) == len({r['path'] for r in index}) == 4
    with pytest.raises(ValueError, match='ambiguous'):
        resolve_run_dir('run_0001', root)
    qualified = 'shop/agente/gemini/run_0001'
    assert resolve_run_dir(qualified, root) == root / qualified
    assert paths(qualified, root, tmp_path / 'features', 'l3_browser_dynamic.json')[1] == tmp_path / 'features' / qualified / 'l3_browser_dynamic.json'
    assert prepare(root, apply=True)['renamed'] == []
    assert prepare_stable(root, apply=True)['renamed'] == []
    dataset = tmp_path / 'dataset'
    assert build_dataset(root, dataset)['included'] == 4
    sessions = load_sessions(dataset / 'samples.json')
    assert len({s.session_id for s in sessions}) == 4
    assert qualified in {s.session_id for s in sessions}
    with pytest.raises(ValueError, match='relative path'):
        resolve_run_dir('../outside', root)
