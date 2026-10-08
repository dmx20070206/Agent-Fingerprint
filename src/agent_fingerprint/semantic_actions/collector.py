"""Compose a collector plugin through the explicit versioned monitor API."""
from pathlib import Path
from agent_fingerprint.paths import PACKAGE_ROOT


def build_probe(source=None):
    if source is None:
        source = (PACKAGE_ROOT / "collection/probes/inject_monitor.js").read_text(encoding="utf-8")
    if "// AF_MONITOR_EXTENSION_API:1" not in source:
        raise ValueError("Collector hook drift: unsupported monitor extension API")
    extension = Path(__file__).with_name("collector_extension.js").read_text(encoding="utf-8")
    return extension + "\n" + source
