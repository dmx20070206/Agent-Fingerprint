"""Organize runs without changing identity; changes require explicit apply."""
import json
import re
from pathlib import Path

from .io import atomic_json
from .metadata import site_from_manifest, task_metadata
from .normalize_run_ids import normalize
from .run_store import iter_run_manifests


def prepare_stable(root, *, apply=False, index=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    index = Path(index).resolve() if index else root / "index.jsonl"
    previous = index.read_text(encoding="utf-8") if index.exists() else ""
    current = {row["run_id"]: row for row in (json.loads(line) for line in previous.splitlines() if line.strip())}
    plans, entries, seen = [], [], set()
    for path in iter_run_manifests(root):
        source = path.parent
        data = json.loads(path.read_text(encoding="utf-8"))
        run_id = data.get("run_id") or source.name
        agent = data["agent"]["name"]
        model = data["agent"]["model"]
        site = site_from_manifest(data, source)
        for label in (run_id, agent, model, site):
            if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", label):
                raise ValueError(f"Missing or unsafe manifest label: {label!r} ({source})")
        if run_id in seen:
            raise ValueError(f"Duplicate run ID: {run_id}")
        seen.add(run_id)
        target = root / site / agent / model / run_id
        if target != source and target.exists():
            raise FileExistsError(target)
        # Resolve all destinations, labels, index paths and JSON before any mutation.
        entry = {"run_id": run_id, "path": target.relative_to(index.parent).as_posix(),
                 "agent": agent, "model": model, "site": site,
                 "status": data.get("status"), "started_at": data.get("started_at")}
        task = data.get("task") or {}
        metadata = task_metadata(task.get("requested_url") or task.get("url"), task.get("prompt", ""),
                                 task_id=task.get("task_id"), site=site)
        updated = {**data, "run_id": run_id, "task": {**task, **metadata}}
        plans.append((source, target, updated, data != updated))
        entries.append(entry)
    if not plans:
        raise ValueError(f"No manifests found under {root}")
    updates = [row for row in entries if current.get(row["run_id"]) != row]
    normalized = normalize(root, run_ids={str(source): data["run_id"] for source, _, data, _ in plans})
    changes = [{"source": source.relative_to(root).as_posix(), "destination": target.relative_to(root).as_posix(),
                "old_run_id": data["run_id"], "run_id": data["run_id"]}
               for source, target, data, _ in plans if source != target]
    if apply:
        if changes:
            history_path = root / "run_moves.json"
            history = json.loads(history_path.read_text()) if history_path.exists() else []
            atomic_json(history_path, history + changes)
        for source, target, data, changed in plans:
            if changed:
                atomic_json(source / "manifest.json", data)
            # Only inconsistent structured IDs are repaired; raw event content stays intact.
            normalize(source, apply=True, run_ids={str(source): data["run_id"]})
            if source != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
        if updates:
            index.parent.mkdir(parents=True, exist_ok=True)
            text = previous + ("\n" if previous and not previous.endswith("\n") else "")
            text += "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in updates)
            temporary = index.with_name(index.name + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(index)
    return {"applied": apply, "runs_found": len(plans), "renamed": changes,
            "normalized_files": normalized, "index_updates": len(updates), "index": str(index),
            "metadata_updates": sum(changed for _, _, _, changed in plans)}
