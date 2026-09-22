"""Unit tests for probe command construction and trace probe shape."""

from __future__ import annotations

import tempfile
import unittest
import socket
from pathlib import Path

from probes.traffic_sniffer import CaptureResult, TrafficSniffer


class TrafficSnifferTest(unittest.TestCase):
    def test_tcpdump_sigint_is_a_normal_shutdown(self) -> None:
        result = CaptureResult(
            "tcpdump", ("tcpdump",), Path("/tmp/traffic.pcap"), Path("/tmp"),
            "start", "stop", -2, 0.1, Path("/tmp/out"), Path("/tmp/err")
        )
        self.assertTrue(result.success)

    def test_tcpdump_command_is_shell_free_and_writes_pcap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sniffer = TrafficSniffer(
                directory,
                backend="tcpdump",
                executable="tcpdump",
                interface="any",
                capture_filter="tcp port 8080",
                snaplen=128,
            )
            self.assertEqual(
                sniffer.command(),
                [
                    "tcpdump", "-U", "-n", "-w", str(Path(directory).resolve() / "traffic.pcap"),
                    "-i", "any", "-s", "128", "tcp", "port", "8080",
                ],
            )

    def test_only_tcpdump_backend_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                TrafficSniffer(directory, backend="mitmproxy")
            self.assertEqual(TrafficSniffer(directory).backend, "tcpdump")

    def test_startup_timeout_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                TrafficSniffer(directory, startup_timeout=0)

    def test_start_failure_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sniffer = TrafficSniffer(directory, executable="definitely-not-a-capture-tool")
            with self.assertRaises(Exception) as error:
                sniffer.start()
            self.assertIn("unable to start", str(error.exception))


class InjectMonitorTest(unittest.TestCase):
    def test_monitor_is_zero_config_and_protects_sensitive_values(self) -> None:
        source = Path("probes/inject_monitor.js").read_text(encoding="utf-8")
        for method in ("start", "stop", "clear", "getEvents", "export"):
            self.assertIn(method, source)
        self.assertIn("value_length", source)
        self.assertNotIn("value: event.target.value", source)
        self.assertIn('record("pointermove"', source)
        for option in ("capturePointerMoves", "captureKeyboard", "captureMutations", "flushIntervalMs"):
            self.assertNotIn(option, source)
        for event in ("touchstart", "selectionchange", "resize", "mutation", "paste", "wheel"):
            self.assertIn(event, source)
        self.assertIn("MAX_BUFFER", source)
        self.assertIn("scheduleFlush", source)
        self.assertIn("global.fetch", source)
        self.assertIn("navigator.sendBeacon", source)
        self.assertIn("UPLOAD_TIMEOUT_MS", source)
        self.assertIn("MAX_BATCH_EVENTS", source)
        self.assertIn("protocol_version: PROTOCOL_VERSION", source)
        self.assertIn("acknowledgement.ack_seq", source)
        self.assertNotIn("if (accepted) sentThrough", source)
        self.assertIn("agent-fingerprint-ui-trace/v1", source)
        self.assertIn("BLOCKED_DURING_STARTUP", source)
        self.assertIn('record("interaction_ready"', source)
        self.assertIn("__AGENT_FINGERPRINT_READY__", source)
        self.assertIn('document.readyState === "complete"', source)
        self.assertIn('global.addEventListener("load"', source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
