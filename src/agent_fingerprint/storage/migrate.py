"""Explicit, copy-first migration from the historical v1 artifact layout."""
import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from .artifacts import _organize_run_files, _canonical_run_files
from .io import write_json
from .metadata import site_from_manifest, task_metadata
from .run_store import _append_run_index


def migrate(source, root, *, apply=False):
    source, root = Path(source).resolve(), Path(root).resolve()
    data = json.loads((source / "manifest.json").read_text())
    if data.get("schema") == "agent-fingerprint-run/v2":
        return {"status": "unchanged", "source": str(source)}
    if data.get("schema") != "agent-fingerprint-run/v1":
        raise ValueError(f"Unsupported run schema: {data.get('schema')}")
    day = str(data["started_at"])[:10]
    from datetime import date
    date.fromisoformat(day)
    run_id = data.get("run_id") or source.name
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id):
        raise ValueError("Unsafe run ID")
    target = root / day / run_id
    if target.exists():
        raise FileExistsError(target)
    report = {"status": "planned", "source": str(source), "destination": str(target)}
    if not apply:
        return report
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".migrate-", dir=target.parent) as temporary:
        staging = Path(temporary) / run_id
        shutil.copytree(source, staging, symlinks=True)
        # Retain an exact pre-migration copy, including unknown framework files.
        backup = staging / "artifacts/legacy-v1"
        shutil.copytree(source, backup, symlinks=True)
        capture = staging / "fingerprints/traffic.pcap"
        if capture.is_file():
            network = staging / "artifacts/network"
            network.mkdir(parents=True, exist_ok=True)
            shutil.copy2(capture, network / "traffic.pcap")
            capture.unlink()
        organized = _organize_run_files(staging, agent_result=None, capture_result=None, gateway=None)
        native = organized["native_result"]
        execution = dict(data.get("agent_result") or {})
        command = execution.pop("command", None)
        if command is not None:
            execution["command_sha256"] = hashlib.sha256(json.dumps(command).encode()).hexdigest()
        task = data.get("task") or {}
        migrated = {k: v for k, v in data.items() if k != "agent_result"}
        migrated.update(schema="agent-fingerprint-run/v2", run_id=run_id, execution=execution,
                        task={**task, **task_metadata(task.get("url"), task.get("prompt", ""),
                              task_id=task.get("task_id"), site=site_from_manifest(data, source))},
                        outcome={"output": native.get("output", native.get("answer")),
                                 "success": native.get("task_success", execution.get("task_success")),
                                 "framework_status": native.get("task_status")},
                        artifacts={**organized["artifacts"], "legacy_backup": "artifacts/legacy-v1"},
                        logs=organized["logs"], collector={"version": "unknown"})
        migrated["files"] = _canonical_run_files(staging, include_manifest=True)
        write_json(staging / "manifest.json", migrated)
        staging.rename(target)
        # Keep the original until the destination and index are durable.
        _append_run_index(root, target, migrated)
        shutil.rmtree(source)
    return {**report, "status": "migrated"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/runs"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    runs = [p.parent for p in args.root.glob("*/manifest.json")]
    print(json.dumps([migrate(run, args.root, apply=args.apply) for run in runs], indent=2))
