"""Stable run identity, explicit task labels and acquisition provenance."""
import hashlib
import json
import re
from urllib.parse import urlsplit


def task_metadata(url, prompt, *, task_id=None, site=None):
    parsed = urlsplit(url or "")
    path = parsed.path
    if site is None:
        for name in ("flights", "shop", "forums", "keyboard", "mouse_move", "mouse_click", "wheel_scroll", "wikipedia"):
            if name in path.replace("-", "_"):
                site = "forum" if name == "forums" else name
                break
        else:
            site = parsed.hostname or "unknown"
    key = [parsed.hostname, path, parsed.query, prompt]
    return {"task_id": task_id or hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16], "site": site}


def collector_metadata(version="legacy/v2"):
    from agent_fingerprint.paths import PACKAGE_ROOT
    source = PACKAGE_ROOT / "collection/probes/inject_monitor.js"
    payload = source.read_bytes()
    if version == "semantic/v1":
        from agent_fingerprint.semantic_actions.collector import build_probe
        payload = build_probe().encode()
    return {"version": version, "sha256": hashlib.sha256(payload).hexdigest()}


def site_from_manifest(manifest, directory=None):
    task = manifest.get("task") or {}
    site = task.get("site") or manifest.get("site")
    if site:
        return site
    # Legacy fallback only; new collection manifests always carry an explicit site.
    if directory is not None:
        from pathlib import Path
        run = Path(directory)
        agent = manifest.get("agent") or {}
        if run.parent.name == agent.get("model") and run.parent.parent.name == agent.get("name"):
            return run.parent.parent.parent.name
        match = re.search(r"_(flights|shop|forums?|keyboard|mouse_move|mouse_click|wheel_scroll)(?:_\d+)?$", run.name)
        if match:
            return "forum" if match[1] == "forums" else match[1]
    url = task.get("requested_url") or task.get("url")
    return task_metadata(url, task.get("prompt", ""))["site"] if url else None
