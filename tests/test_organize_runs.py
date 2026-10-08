import json

import pytest

from agent_fingerprint.analysis._common import resolve_run_dir
from agent_fingerprint.storage.organize_runs import organize


def make_run(root, name='browseruse_chat_gpt_flights_1'):
    run = root / name
    run.mkdir(parents=True)
    (run / 'manifest.json').write_text(json.dumps({
        'run_id': name, 'agent': {'name': 'browseruse', 'model': 'chat-gpt'}}))
    (run / 'capture.bin').write_bytes(b'original bytes\x00')
    return run


def test_preview_move_index_and_repeat(tmp_path):
    root = tmp_path / 'final'
    run = make_run(root)
    original = (run / 'manifest.json').read_bytes()
    index = tmp_path / 'index.jsonl'
    index.write_text(json.dumps({'run_id': run.name, 'path': 'old/missing'}) + '\n')
    assert len(organize(root, index=index)) == 1
    assert run.exists()
    organize(root, apply=True, index=index)
    target = root / 'flights/browseruse/chat-gpt' / run.name
    assert not run.exists()
    assert (target / 'manifest.json').read_bytes() == original
    assert (target / 'capture.bin').read_bytes() == b'original bytes\x00'
    assert resolve_run_dir(run.name, tmp_path) == target
    assert resolve_run_dir(run.name, root) == target
    assert organize(root, apply=True, index=index) == []


def test_conflict_preflight_does_not_move_runs(tmp_path):
    first = make_run(tmp_path, 'browseruse_chat_gpt_flights_1')
    second = make_run(tmp_path, 'browseruse_chat_gpt_flights_2')
    make_run(tmp_path / 'flights/browseruse/chat-gpt', second.name)
    with pytest.raises(FileExistsError):
        organize(tmp_path, apply=True)
    assert first.exists() and second.exists()


def test_recursive_lookup_rejects_ambiguous_ids(tmp_path):
    first = make_run(tmp_path / 'one')
    make_run(tmp_path / 'two', first.name)
    with pytest.raises(ValueError, match='ambiguous'):
        resolve_run_dir(first.name, tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_run_dir('missing', tmp_path)
