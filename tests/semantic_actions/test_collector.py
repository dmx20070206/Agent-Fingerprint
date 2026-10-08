"""Execute the composed collector against DOM event/state objects in Node."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_fingerprint.semantic_actions import parse_semantic_actions
from agent_fingerprint.semantic_actions.collector import build_probe


def test_source_hook_drift_fails_loudly():
    with pytest.raises(ValueError, match="hook drift"):
        build_probe("unrelated source")


def test_existing_sandbox_can_serve_composed_probe(tmp_path, monkeypatch):
    import threading
    from urllib.request import build_opener, ProxyHandler
    from agent_fingerprint.collection.sandbox import SandboxRequestHandler, MONITOR_SCRIPT_ENDPOINT, create_server

    path = tmp_path / "monitor.js"
    source = build_probe()
    path.write_text(source)
    monkeypatch.setattr(SandboxRequestHandler, "monitor_path", path)
    (tmp_path / "index.html").write_text("<html><head></head><body>fixture</body></html>")
    server = create_server(port=0, directory=tmp_path, inject_monitor=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + MONITOR_SCRIPT_ENDPOINT, timeout=3) as response:
            assert response.read().decode() == source
        with opener.open(base + "/index.html", timeout=3) as response:
            assert MONITOR_SCRIPT_ENDPOINT in response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_generated_probe_records_semantic_evidence_without_values(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node is required to execute the browser probe harness")
    probe = tmp_path / "probe.js"
    probe.write_text(build_probe())
    harness = Path(__file__).with_name("collector_harness.cjs")
    run = subprocess.run(["node", str(harness), str(probe)], check=True, capture_output=True, text=True)
    events = json.loads(run.stdout)
    serialized = json.dumps(events)
    assert "SECRET" not in serialized
    assert events[0]["semantic_tracking"] is True
    assert all(e.get("session_id") and e.get("document_id") for e in events)
    assert {"beforeinput", "compositionstart", "compositionupdate", "compositionend", "scrollend"} <= {e["type"] for e in events}
    click = next(e for e in events if e["type"] == "click")
    assert [n["tag"] for n in click["composed_path"]][:3] == ["svg", "span", "button"]
    assert any(e.get("checked_changed") is True for e in events)
    assert any(e.get("value_changed") is True for e in events)
    assert any(e.get("target", {}).get("tag") == "div" for e in events if e["type"] == "scroll")
    assert any(e.get("semantic_event_type") == "pointercancel" for e in events)
    result = parse_semantic_actions({"events": events})
    kinds = [a.action_type for a in result.actions]
    assert "SELECT" in kinds and "TOGGLE" in kinds and "TEXT_ENTRY" in kinds
    assert [a.target_role for a in result.actions if a.action_type == "SCROLL"] == ["PAGE", "CONTAINER"]
    assert len([a for a in result.actions if a.action_type == "TOGGLE"]) == 1
    assert len([a for a in result.actions if a.action_type == "SELECT"]) == 1
    pointer = next(e for e in result.normalized_events if e.event_type == "pointerdown")
    mouse = next(e for e in result.normalized_events if e.event_type == "mousedown")
    assert mouse.duplicate_of == pointer.index
