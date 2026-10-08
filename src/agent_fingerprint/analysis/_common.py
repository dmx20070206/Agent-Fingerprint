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




from agent_fingerprint.storage.run_store import resolve_run_dir
from agent_fingerprint.storage.io import read_json, write_json
