"""Regression contracts for experiment configuration and stable run storage."""
import json

import pytest
import yaml

from agent_fingerprint.analysis.dataset import build_dataset
from agent_fingerprint.cli.collect import _apply_cli_defaults, _build_parser
from agent_fingerprint.cli.experiment import build_plan, resolve_option
from agent_fingerprint.paths import PROJECT_ROOT
from agent_fingerprint.storage.prepare_runs import prepare
from agent_fingerprint.storage.run_store import resolve_run_dir


def make_run(root, directory="arbitrary-folder", identity="stable-id"):
    run = root / directory
    (run / "fingerprints").mkdir(parents=True)
    manifest = {"run_id": identity, "agent": {"name": "browseruse", "model": "claude"},
                "task": {"task_id": "task-1", "site": "flights"}, "status": "success"}
    raw = {"run_id": identity, "events": [{"type": "click", "session_id": "original-session"}]}
    (run / "manifest.json").write_text(json.dumps(manifest))
    (run / "fingerprints/l3_browser_dynamic.json").write_text(json.dumps(raw))
    return run


def test_all_experiment_combinations_parse(monkeypatch):
    for key in ("AGENTE_FLIGHTS_TIMEOUT", "AUTOGEN_FLIGHTS_TIMEOUT", "BROWSER_USE_FLIGHTS_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    config = yaml.safe_load((PROJECT_ROOT / "configs/experiments/default.yaml").read_text())
    plan = build_plan(config, tasks=list(config["tasks"]), repeats=1)
    parser = _build_parser()
    for row in plan:
        args = parser.parse_args(row["argv"])
        assert args.timeout > 0
        assert args.site
    assert len(plan) == len(config["agents"]) * len(config["models"]) * len(config["tasks"])


def test_environment_options_are_resolved_without_shell(monkeypatch):
    monkeypatch.setenv("AF_TEST_TIMEOUT", "812")
    assert resolve_option("${AF_TEST_TIMEOUT:-3600}") == "812"
    monkeypatch.setenv("AF_TEST_TIMEOUT", "")
    assert resolve_option("${AF_TEST_TIMEOUT:-3600}") == "3600"
    assert resolve_option("$(touch never-execute)") == "$(touch never-execute)"
    with pytest.raises(ValueError):
        resolve_option("${UNSUPPORTED}")


def test_cli_labels_reach_matrix_tasks():
    args = _build_parser().parse_args(["--site", "flights", "--task-id", "shared-task"])
    tasks = [{"url": "/", "prompt": "one"}, {"url": "/", "prompt": "two", "task_id": "explicit"}]
    result = _apply_cli_defaults(tasks, args)
    assert [task["site"] for task in result] == ["flights", "flights"]
    assert [task["task_id"] for task in result] == ["shared-task", "explicit"]
    assert "site" not in tasks[0]


def test_stable_identity_survives_move_and_ignores_backup(tmp_path):
    root = tmp_path / "runs"
    run = make_run(root)
    raw = (run / "fingerprints/l3_browser_dynamic.json").read_bytes()
    backup = run / "artifacts/legacy-v1"
    backup.mkdir(parents=True)
    (backup / "manifest.json").write_text('{"run_id": "original-backup"}')
    (backup / "trace.json").write_text('{"run_id": "original-backup"}')
    assert resolve_run_dir("stable-id", root) == run
    assert build_dataset(root, tmp_path / "before")["included"] == 1
    preview = prepare(root)
    assert run.exists()
    assert preview["runs_found"] == 1
    report = prepare(root, apply=True)
    assert report["renamed"] == preview["renamed"]
    moved = resolve_run_dir("stable-id", root)
    assert moved != run
    assert (moved / "fingerprints/l3_browser_dynamic.json").read_bytes() == raw
    assert (moved / "artifacts/legacy-v1/trace.json").read_text() == '{"run_id": "original-backup"}'
    again = prepare(root, apply=True)
    assert again["renamed"] == again["normalized_files"] == []
    assert again["metadata_updates"] == again["index_updates"] == 0
    assert build_dataset(root, tmp_path / "after")["included"] == 1


def test_stable_prepare_duplicate_identity_is_non_mutating(tmp_path):
    first = make_run(tmp_path, "one")
    make_run(tmp_path, "two")
    before = (first / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="Duplicate run ID"):
        prepare(tmp_path, apply=True)
    assert (first / "manifest.json").read_bytes() == before
    assert not (tmp_path / "run_moves.json").exists()
