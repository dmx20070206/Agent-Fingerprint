"""Shared CLI/path helpers for fingerprint analysis scripts."""

import argparse
import json
from pathlib import Path


def parser(description=""):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--task-id", required=True)
    p.add_argument("--input-dir", type=Path, default=Path("data/runs"))
    p.add_argument("--output-dir", type=Path, default=Path("data/results"))
    return p


def paths(task_id, input_dir, output_dir, filename):
    root = resolve_run_dir(task_id, input_dir)
    # Canonical layout: raw fingerprints are grouped under fingerprints/.
    source = root / "fingerprints" / filename
    if not source.is_file():
        source = root / filename  # compatibility with older runs
    destination = Path(output_dir) / task_id / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    return source, destination


def resolve_run_dir(task_id, input_dir):
    input_root = Path(input_dir).expanduser().resolve()
    legacy = input_root / task_id
    if legacy.is_dir():
        return legacy
    index = input_root / "index.jsonl"
    if index.is_file():
        for line in reversed(index.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if entry.get("run_id") != task_id or not isinstance(entry.get("path"), str):
                continue
            candidate = (input_root / entry["path"]).resolve()
            try:
                candidate.relative_to(input_root)
            except ValueError:
                continue
            if candidate.is_dir():
                return candidate
    raise FileNotFoundError(f"run not found: {task_id}")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
