import json

import pytest

from agent_fingerprint.analysis.dataset_audit import (
    L3_FILE,
    audit_run,
    discover,
    filter_reasons,
    main,
    quarantine_runs,
)


def write_run(
    root,
    name="flights/browseruse/model/run_0001",
    *,
    count=1000,
    status="success",
    verified=None,
):
    path = root / name
    (path / "fingerprints").mkdir(parents=True)
    manifest = {
        "run_id": path.name,
        "agent": {"name": "browseruse", "model": "model"},
        "status": status,
        "outcome": {"verification": {"status": verified}},
    }
    (path / "manifest.json").write_text(json.dumps(manifest))
    (path / L3_FILE).write_text(
        json.dumps({"event_count": count, "events": [{}] * count})
    )
    return path


def snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_cli_default_is_read_only_and_prints_counts(tmp_path, capsys):
    root = tmp_path / "runs"
    write_run(root, count=999)
    write_run(root, "shop/browseruse/model/run_0001", count=1000, status="failed")
    before = snapshot(tmp_path)
    assert main(["--input-dir", str(root)]) == 0
    output = capsys.readouterr().out
    assert "收集数" in output
    assert "browseruse  model  2" in output
    assert (
        "flights/browseruse/model/run_0001/fingerprints/l3_browser_dynamic.json"
        in output
    )
    assert "999" in output and "1000" in output
    assert "成功" in output and "失败" in output
    assert "符合筛选条件 2 条" in output
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "count,status,verified,selected,result",
    [
        (999, "success", "passed", True, "成功"),
        (1000, "success", "passed", False, "成功"),
        (1001, "failed", None, True, "失败"),
        (1001, "success", "failed", True, "失败"),
        (2, "running", None, False, "进行中"),
        (1000, "unknown", None, False, "未知"),
    ],
)
def test_threshold_and_task_outcome(
    tmp_path, count, status, verified, selected, result
):
    row = audit_run(
        write_run(tmp_path, count=count, status=status, verified=verified), tmp_path
    )
    assert row["task_result"] == result
    assert bool(filter_reasons(row)) is selected


def test_reads_actual_array_length_and_reports_conflict(tmp_path):
    path = write_run(tmp_path, count=50)
    (path / L3_FILE).write_text(json.dumps({"event_count": 3000, "events": [{}] * 49}))
    row = audit_run(path, tmp_path)
    assert row["event_count"] == 49
    assert row["notes"]
    assert filter_reasons(row, 50, l3_only=True) == ["L3 < 50"]


def test_corrupt_and_orphan_runs_are_shown_and_artifacts_are_skipped(tmp_path, capsys):
    root = tmp_path / "runs"
    bad = write_run(root, "bad")
    (bad / "manifest.json").write_text("[]")
    (bad / L3_FILE).write_text("[")
    missing = write_run(root, "missing")
    (missing / L3_FILE).unlink()
    orphan = write_run(root, "orphan")
    (orphan / "manifest.json").unlink()
    write_run(bad / "artifacts", "internal")
    assert len(list(discover(root))) == 3
    assert main(["--input-dir", str(root)]) == 0
    output = capsys.readouterr().out
    assert "manifest 无法读取" in output and "L3 无法计数" in output


def test_filter_moves_whole_runs_and_updates_index_by_path(tmp_path, capsys):
    root, dest = tmp_path / "runs", tmp_path / "quarantine"
    short = write_run(root, count=999)
    failed = write_run(root, "shop/browseruse/model/run_0001", status="failed")
    keep = write_run(root, "flights/browseruse/model/run_0002")
    active = write_run(root, "active", count=0, status="running")
    (short / "logs").mkdir()
    (short / "logs/agent.log").write_text("retain evidence")
    entries = [
        {"path": str(p.relative_to(root)), "run_id": p.name}
        for p in (short, failed, keep, active)
    ]
    (root / "index.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    before = {p: snapshot(p) for p in (short, failed)}
    assert (
        main(["--input-dir", str(root), "--filter", "--quarantine-dir", str(dest)]) == 0
    )
    for path in (short, failed):
        assert not path.exists()
        assert snapshot(dest / path.relative_to(root)) == before[path]
    assert keep.exists() and active.exists()
    remaining = [
        json.loads(line) for line in (root / "index.jsonl").read_text().splitlines()
    ]
    assert remaining == entries[2:]
    removed = [
        json.loads(line) for line in (dest / "index.jsonl").read_text().splitlines()
    ]
    assert removed == entries[:2]
    assert len(json.loads((dest / "moves.json").read_text())["runs"]) == 2
    assert len(list(discover(tmp_path))) == 2  # quarantine is excluded
    assert "过滤后" in capsys.readouterr().out


def test_l3_only_50_keeps_failed_long_runs_and_exact_boundary(tmp_path):
    root = tmp_path / "runs"
    short = write_run(root, "short", count=49)
    boundary = write_run(root, "boundary", count=50)
    failed = write_run(root, "failed", count=1200, status="failed")
    args = ["--input-dir", str(root), "--filter", "--l3-only", "--min-events", "50"]
    before = snapshot(tmp_path)
    assert main([*args, "--dry-run"]) == 0
    assert snapshot(tmp_path) == before
    assert main([*args, "--quarantine-dir", str(tmp_path / "quarantine")]) == 0
    assert not short.exists()
    assert boundary.exists() and failed.exists()


@pytest.mark.parametrize("destination", ["runs/quarantine", "runs", ".", "existing"])
def test_unsafe_destination_rejected_before_mutation(tmp_path, destination):
    root = tmp_path / "runs"
    path = write_run(root, count=1)
    (tmp_path / "existing").mkdir()
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        quarantine_runs(root, tmp_path / destination, [audit_run(path, root)])
    assert snapshot(tmp_path) == before


def test_external_l3_reference_is_not_read(tmp_path, capsys):
    root = tmp_path / "runs"
    path = write_run(root)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["fingerprints"] = {"l3": str(tmp_path / "external.json")}
    (path / "manifest.json").write_text(json.dumps(manifest))
    row = audit_run(path, root)
    assert row["event_count"] is None
    assert main(["--input-dir", str(root)]) == 0
    assert "L3 路径超出运行目录" in capsys.readouterr().out


def test_bad_index_prevents_moves(tmp_path):
    root = tmp_path / "runs"
    path = write_run(root, count=1)
    (root / "index.jsonl").write_text("{bad json")
    with pytest.raises(ValueError):
        quarantine_runs(root, tmp_path / "quarantine", [audit_run(path, root)])
    assert path.exists()
    assert not (tmp_path / "quarantine").exists()


def test_changed_run_prevents_moves(tmp_path):
    root = tmp_path / "runs"
    path = write_run(root, count=1)
    row = audit_run(path, root)
    (path / "manifest.json").write_text('{"status":"running"}')
    with pytest.raises(ValueError, match="发生变化"):
        quarantine_runs(root, tmp_path / "quarantine", [row])
    assert path.exists()
