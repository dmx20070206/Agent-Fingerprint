"""Run identity lookup and append-only location index."""
import json
import os
from pathlib import Path
from typing import Any, Mapping
from .schema import RUN_INDEX_FILE


def iter_run_manifests(root):
    """Stop at a run boundary; framework artifacts and backups are not runs."""
    for directory, children, files in os.walk(Path(root)):
        children.sort()
        if "manifest.json" in files:
            children.clear()
            yield Path(directory) / "manifest.json"


def _has_run_id(directory, run_id):
    path = directory / "manifest.json"
    if not path.exists():
        return directory.name == run_id  # legacy traces without manifests
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("run_id", directory.name) == run_id
    except (ValueError, OSError):
        return False

def _append_run_index(output_root: Path, run_dir: Path, manifest: Mapping[str, Any]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": manifest.get("run_id"),
        "path": str(run_dir.relative_to(output_root)),
        "status": manifest.get("status"),
        "started_at": manifest.get("started_at"),
        "agent": (manifest.get("agent") or {}).get("name"),
        "model": (manifest.get("agent") or {}).get("model"),
    }
    encoded = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(output_root / RUN_INDEX_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)




def resolve_run_dir(task_id, input_dir):
    input_root = Path(input_dir).expanduser().resolve()
    legacy = input_root / task_id
    if legacy.is_dir() and _has_run_id(legacy, task_id):
        return legacy
    index = input_root / "index.jsonl"
    if index.is_file():
        for line in reversed(index.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("run_id") != task_id or not isinstance(entry.get("path"), str):
                continue
            candidate = (input_root / entry["path"]).resolve()
            try:
                candidate.relative_to(input_root)
            except ValueError:
                continue
            if candidate.is_dir() and _has_run_id(candidate, task_id):
                return candidate
    # Curated runs can be grouped by task/agent/model without an index.
    matches = sorted({p.parent.resolve() for p in iter_run_manifests(input_root)
                      if _has_run_id(p.parent, task_id) and p.parent.resolve().is_relative_to(input_root)})
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(f"ambiguous run ID {task_id}: {matches}; narrow --input-dir")
    raise FileNotFoundError(f"run not found: {task_id}")
