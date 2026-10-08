"""Black-box pipeline tests with local stand-ins for paid/system services.

The tests in this module intentionally exercise process and HTTP boundaries:

* :class:`PipelineRunner` starts the real sandbox HTTP server;
* ``FakeGateway`` is an in-process OpenAI-compatible HTTP endpoint (it never
  calls a provider or an LLM);
* ``SubprocessAgent`` is a real child Python process and talks to both HTTP
  endpoints using only the standard library; and
* ``FakeCapture`` stands in for tcpdump, producing the same normalized result
  and artifact shape as the real probe.

This gives us an inexpensive full-cycle test while keeping CI independent of
Conda, browser binaries, tcpdump privileges, and API credentials.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent_fingerprint.adapters.base_adapter import AgentResult, BaseAgentAdapter
from agent_fingerprint.collection.runner import PipelineRunner
from agent_fingerprint.collection.probes.traffic_sniffer import CaptureResult


class _FakeGatewayHandler(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible endpoint used by ``FakeGateway``."""

    server: "_FakeGatewayHTTPServer"

    def log_message(self, *_args: object) -> None:  # pragma: no cover - noisy stdlib hook
        return

    def _json(self, status: int, value: dict[str, Any]) -> None:
        encoded = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        if self.path == "/health/liveliness":
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        started = time.perf_counter_ns()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return
        # Store only non-sensitive request metadata.  The fake gateway is
        # intentionally shaped like the timing log a real proxy would emit.
        finished = time.perf_counter_ns()
        event = {
            "event": "completion",
            "model": payload.get("model"),
            "request_started_ns": started,
            "response_finished_ns": finished,
            "latency_ms": (finished - started) / 1_000_000,
        }
        self.server.events.append(event)
        self.server.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.server.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        self._json(
            200,
            {
                "id": "fake-completion",
                "object": "chat.completion",
                "model": payload.get("model", "fake-model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "DONE"}, "finish_reason": "stop"}],
            },
        )


class _FakeGatewayHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], log_path: Path) -> None:
        self.events: list[dict[str, Any]] = []
        self.log_path = log_path
        super().__init__(address, _FakeGatewayHandler)


class FakeGateway:
    """Drop-in gateway object accepted by ``PipelineRunner.gateway_factory``."""

    def __init__(self, run_dir: Path, lifecycle: list[str]) -> None:
        self.run_dir = Path(run_dir)
        self.lifecycle = lifecycle
        self.log_path = self.run_dir / "gateway_events.jsonl"
        self.server = _FakeGatewayHTTPServer(("127.0.0.1", 0), self.log_path)
        self.thread: threading.Thread | None = None
        self.started = False

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    def latency_log_path(self) -> Path:
        return self.log_path

    def start(self) -> "FakeGateway":
        if self.started:
            raise RuntimeError("fake gateway already started")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.started = True
        self.lifecycle.append("gateway.start")
        return self

    def stop(self) -> None:
        if not self.started:
            return
        self.lifecycle.append("gateway.stop")
        self.server.shutdown()
        self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)
        self.started = False


class FakeCapture:
    """A tcpdump-shaped capture replacement with deterministic artifacts."""

    def __init__(self, output_dir: Path, lifecycle: list[str]) -> None:
        self.output_dir = Path(output_dir)
        self.lifecycle = lifecycle
        self.output_path = self.output_dir / "traffic.pcap"
        self.stopped = False

    def start(self) -> "FakeCapture":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path.write_bytes(b"FAKE-PCAP\n")
        (self.output_dir / "sniffer.stdout.log").write_text("fake capture started\n", encoding="utf-8")
        (self.output_dir / "sniffer.stderr.log").write_text("", encoding="utf-8")
        self.lifecycle.append("capture.start")
        return self

    def stop(self) -> CaptureResult:
        if self.stopped:
            # PipelineRunner only stops once; retaining this guard makes the
            # fake useful in context-manager tests as well.
            return self.result
        self.stopped = True
        self.lifecycle.append("capture.stop")
        self.result = CaptureResult(
            backend="tcpdump",
            command=("fake-tcpdump", "-w", str(self.output_path)),
            output_path=self.output_path,
            output_dir=self.output_dir,
            started_at="2026-01-01T00:00:00+00:00",
            stopped_at="2026-01-01T00:00:00.010000+00:00",
            returncode=0,
            duration_seconds=0.01,
            stdout_log=self.output_dir / "sniffer.stdout.log",
            stderr_log=self.output_dir / "sniffer.stderr.log",
        )
        (self.output_dir / "traffic_capture.json").write_text(
            json.dumps(self.result.to_dict(), indent=2), encoding="utf-8"
        )
        return self.result


class SubprocessAgent(BaseAgentAdapter):
    """A real child process that performs the minimum Agent interactions."""

    adapter_name = "fake-subprocess-agent"

    # This script deliberately imports no project code.  It emulates what an
    # isolated Web Agent would do: GET a page, send UI events, then request a
    # model completion through the OpenAI-compatible gateway.
    SCRIPT = r'''
import json
import os
import sys
import urllib.request
from pathlib import Path

url = os.environ["FAKE_AGENT_URL"]
gateway = os.environ["FAKE_GATEWAY_URL"]
run_id = os.environ["FAKE_RUN_ID"]
output = Path(os.environ["AGENT_OUTPUT_DIR"])
output.mkdir(parents=True, exist_ok=True)

# Do not let a developer/CI HTTP proxy intercept loopback traffic.  This is
# important because the child process inherits proxy variables from its parent.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(url, timeout=3) as response:
    body = response.read().decode("utf-8")
assert "极简控制台" in body
assert "/__agent_fingerprint__/monitor.js" in body

events_url = url.rsplit("/", 1)[0] + "/__agent_fingerprint__/events"
events = {
    "run_id": run_id,
    "session_id": "fake-session",
    "events": [
        {"seq": 1, "type": "monitor_start", "epoch_ms": 1},
        {"seq": 2, "type": "click", "epoch_ms": 2, "target": {"tag": "button", "id": "confirm"}},
    ],
}
request = urllib.request.Request(events_url, data=json.dumps(events).encode(), headers={"Content-Type": "application/json"}, method="POST")
with opener.open(request, timeout=3) as response:
    assert response.status == 200

completion = {"model": os.environ.get("FAKE_MODEL", "fake-model"), "messages": [{"role": "user", "content": "redacted"}]}
request = urllib.request.Request(gateway + "/chat/completions", data=json.dumps(completion).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"}, method="POST")
with opener.open(request, timeout=3) as response:
    answer = json.loads(response.read().decode())
assert answer["choices"][0]["message"]["content"] == "DONE"

(output / "result.json").write_text(
    json.dumps({
        "url": url,
        "gateway": gateway,
        "answer": answer,
        "task_success": True,
        "task_status": "synthetic_complete",
    }),
    encoding="utf-8",
)
print(json.dumps({"child_pid": os.getpid(), "gateway_request": True}))
'''

    def __init__(self, gateway_url: str, run_id: str, model: str, lifecycle: list[str]) -> None:
        # ``conda_env=None`` is intentional: the test still uses the adapter's
        # process boundary, but runs with the current interpreter in CI.
        super().__init__(conda_env=None, executable=sys.executable, timeout=10)
        self.gateway_url = gateway_url
        self.run_id = run_id
        self.model = model
        self.lifecycle = lifecycle

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        path = self.prepare_output_dir(output_dir)
        self.lifecycle.append("agent.start")
        return self.execute(
            [sys.executable, "-c", self.SCRIPT],
            output_dir=path,
            env=self.merged_environment(
                {
                    "FAKE_AGENT_URL": url,
                    "FAKE_GATEWAY_URL": self.gateway_url,
                    "FAKE_RUN_ID": self.run_id,
                    "FAKE_MODEL": self.model,
                    "AGENT_OUTPUT_DIR": str(path),
                }
            ),
        )


class PipelineIntegrationTest(unittest.TestCase):
    def test_full_cycle_crosses_http_and_process_boundaries(self) -> None:
        """Run one complete cycle without an LLM key or system tcpdump."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lifecycle: list[str] = []
            gateway_holder: dict[str, FakeGateway] = {}
            adapter_holder: dict[str, SubprocessAgent] = {}

            def make_gateway(run_dir: Path) -> FakeGateway:
                gateway = FakeGateway(run_dir, lifecycle)
                gateway_holder["gateway"] = gateway
                return gateway

            def make_capture(output_dir: Path) -> FakeCapture:
                return FakeCapture(output_dir, lifecycle)

            # The gateway is created before the adapter by PipelineRunner, so
            # the factory can safely hand its ephemeral port to the child.
            def make_agent() -> SubprocessAgent:
                gateway = gateway_holder["gateway"]
                agent = SubprocessAgent(gateway.base_url, "cycle-http", "fake-alias", lifecycle)
                adapter_holder["agent"] = agent
                return agent

            runner = PipelineRunner(
                output_root=root / "runs",
                gateway_factory=make_gateway,
                sniffer_factory=make_capture,
                adapter_factory={"fake": make_agent},
            )
            # Pass a relative fixture path so the Runner starts the real
            # sandbox and resolves it to the ephemeral server URL itself.
            result = runner.run_once(
                "/01-minimal.html",
                "click the confirm button",
                agent_name="fake",
                model="fake-alias",
                run_id="cycle-http",
            )

            self.assertTrue(result.success, result.manifest)
            self.assertEqual(lifecycle[:3], ["gateway.start", "capture.start", "agent.start"])
            # Capture is stopped before the gateway in the implementation;
            # check the exact cleanup relation rather than assuming unrelated
            # helper events are present at the tail.
            self.assertLess(lifecycle.index("capture.stop"), lifecycle.index("gateway.stop"))

            manifest_path = result.output_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "agent-fingerprint-run/v2")
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["agent"]["model"], "fake-alias")

            # Every canonical file advertised by the manifest is archived in
            # the run directory, including the complete network artifact.
            for relative in manifest["files"]:
                self.assertTrue((result.output_dir / relative).is_file(), relative)
            self.assertTrue(
                (result.output_dir / "artifacts" / "network" / "traffic.pcap")
                .read_bytes()
                .startswith(b"FAKE")
            )

            trace = json.loads(
                (result.output_dir / "fingerprints" / "l3_browser_dynamic.json").read_text(encoding="utf-8")
            )
            self.assertEqual(trace["run_id"], "cycle-http")
            self.assertEqual(trace["event_count"], 2)
            self.assertEqual([event["type"] for event in trace["events"]], ["monitor_start", "click"])

            gateway = gateway_holder["gateway"]
            self.assertEqual(len(gateway.server.events), 1)
            self.assertEqual(gateway.server.events[0]["model"], "fake-alias")
            self.assertGreaterEqual(gateway.server.events[0]["latency_ms"], 0)
            self.assertFalse(gateway.log_path.exists())
            l4 = json.loads(
                (result.output_dir / "fingerprints" / "l4_agent_trace.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(l4["llm_events"][0]["event"], "completion")

            self.assertEqual(manifest["outcome"]["output"]["model"], "fake-alias")
            self.assertFalse((result.output_dir / "agent").exists())
            self.assertIn('"gateway_request": true', result.agent_result.stdout)
            self.assertNotEqual(os.getpid(), json.loads(result.agent_result.stdout)["child_pid"])

            # A server started internally for a one-shot cycle is cleaned up
            # before the result is returned.
            self.assertIsNone(runner._sandbox)

    def test_agent_failure_still_stops_capture_and_writes_failed_manifest(self) -> None:
        """Cleanup and diagnostics survive a child process failure."""

        class FailingAgent(SubprocessAgent):
            def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
                self.validate_task(url, prompt)
                path = self.prepare_output_dir(output_dir)
                self.lifecycle.append("agent.start")
                # Keep ``check=True`` so BaseAgentAdapter raises the same
                # AgentExecutionError a real failed framework would raise.
                return self.execute(
                    [sys.executable, "-c", "import sys; print('intentional failure'); sys.exit(7)"],
                    output_dir=path,
                    env=self.merged_environment(),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lifecycle: list[str] = []
            gateway_holder: dict[str, FakeGateway] = {}

            def make_gateway(run_dir: Path) -> FakeGateway:
                value = FakeGateway(run_dir, lifecycle)
                gateway_holder["gateway"] = value
                return value

            def make_agent() -> FailingAgent:
                return FailingAgent(gateway_holder["gateway"].base_url, "failed-cycle", "fake", lifecycle)

            runner = PipelineRunner(
                output_root=root / "runs",
                gateway_factory=make_gateway,
                sniffer_factory=lambda output_dir: FakeCapture(output_dir, lifecycle),
                adapter_factory={"fake": make_agent},
            )
            result = runner.run_once(
                "/01-minimal.html",
                "fail",
                agent_name="fake",
                run_id="failed-cycle",
            )
            self.assertFalse(result.success)
            self.assertEqual(result.manifest["status"], "failed")
            self.assertIn("return code 7", result.manifest["error"])
            self.assertTrue((result.output_dir / "logs" / "agent.stdout.log").is_file())
            self.assertLess(lifecycle.index("capture.stop"), lifecycle.index("gateway.stop"))
            runner.stop_sandbox_server()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
