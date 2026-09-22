"""Tests for the interactive, human-operated collection adapter."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from adapters.manual_adapter import ManualAdapter
from pipeline import PipelineRunner


class ManualAdapterTest(unittest.TestCase):
    def test_opens_browser_waits_and_writes_success_result(self) -> None:
        opened: list[str] = []
        prompts: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            adapter = ManualAdapter(
                opener=lambda url: opened.append(url) or True,
                completion_waiter=lambda prompt: prompts.append(prompt),
            )
            result = adapter.run_task(
                "http://127.0.0.1:8000/mouse-click.html",
                "click around",
                directory,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.task_status, "manual_complete")
            self.assertEqual(opened, ["http://127.0.0.1:8000/mouse-click.html"])
            self.assertEqual(len(prompts), 1)
            document = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
            self.assertTrue(document["browser_opened"])
            self.assertTrue(document["task_success"])

    def test_no_open_mode_still_waits_for_manual_completion(self) -> None:
        waited: list[bool] = []
        with tempfile.TemporaryDirectory() as directory:
            adapter = ManualAdapter(
                open_browser=False,
                opener=lambda url: self.fail("opener should not be called"),
                completion_waiter=lambda prompt: waited.append(True),
            )
            result = adapter.run_task("http://localhost:8000/", "browse", directory)
            self.assertTrue(result.success)
            self.assertEqual(waited, [True])
            document = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(document["browser_open_requested"])
            self.assertFalse(document["browser_opened"])

    def test_pipeline_collects_manual_browser_events(self) -> None:
        run_id = "manual-pipeline"

        def visit_and_interact(url: str) -> bool:
            opener = build_opener(ProxyHandler({}))
            with opener.open(url, timeout=3) as response:
                html = response.read().decode("utf-8")
            self.assertIn("__AGENT_FINGERPRINT_CONFIG__", html)
            endpoint = url.rsplit("/", 1)[0] + "/__agent_fingerprint__/events"
            payload = {
                "run_id": run_id,
                "session_id": "human-browser",
                "events": [
                    {
                        "seq": 1,
                        "type": "monitor_start",
                        "epoch_ms": 1,
                        "static_fingerprint": {"user_agent": "manual-test-browser"},
                    },
                    {"seq": 2, "type": "click", "epoch_ms": 2},
                ],
            }
            request = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            return True

        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory,
                use_network_probe=False,
                adapter_factory={
                    "manual": lambda: ManualAdapter(
                        opener=visit_and_interact,
                        completion_waiter=lambda prompt: None,
                    )
                },
            )
            result = runner.run_once(
                "/mouse-click.html",
                "interact manually",
                agent_name="manual",
                run_id=run_id,
            )
            self.assertTrue(result.success, result.manifest)
            fingerprints = result.output_dir / "fingerprints"
            static = json.loads((fingerprints / "l2_browser_static.json").read_text(encoding="utf-8"))
            dynamic = json.loads((fingerprints / "l3_browser_dynamic.json").read_text(encoding="utf-8"))
            self.assertEqual(static["sample_count"], 1)
            self.assertEqual(static["samples"][0]["user_agent"], "manual-test-browser")
            self.assertEqual([event["type"] for event in dynamic["events"]], ["monitor_start", "click"])

    def test_pipeline_rejects_empty_manual_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory,
                use_network_probe=False,
                adapter_factory={
                    "manual": lambda: ManualAdapter(
                        open_browser=False,
                        completion_waiter=lambda prompt: None,
                    )
                },
            )
            result = runner.run_once(
                "/mouse-click.html",
                "interact manually",
                agent_name="manual",
                run_id="empty-manual-session",
            )
            self.assertFalse(result.success)
            self.assertIn("no browser events", result.manifest["error"])

    def test_pipeline_waits_for_protocol_v2_final_ack(self) -> None:
        run_id = "manual-final-ack"
        endpoint_holder: list[str] = []
        worker_holder: list[threading.Thread] = []

        def post(endpoint: str, payload: dict) -> dict:
            opener = build_opener(ProxyHandler({}))
            request = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(request, timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))

        def visit(url: str) -> bool:
            endpoint = url.rsplit("/", 1)[0] + "/__agent_fingerprint__/events"
            endpoint_holder.append(endpoint)
            acknowledgement = post(
                endpoint,
                {
                    "protocol_version": 2,
                    "run_id": run_id,
                    "session_id": "browser-v2",
                    "events": [{"seq": 1, "type": "monitor_start", "epoch_ms": 1}],
                },
            )
            self.assertEqual(acknowledgement["ack_seq"], 1)
            return True

        def start_finalizer(_prompt: str) -> None:
            def finalize_when_requested() -> None:
                opener = build_opener(ProxyHandler({}))
                control_url = (
                    endpoint_holder[0]
                    + "?control=1&protocol_version=2&run_id="
                    + run_id
                    + "&session_id=browser-v2"
                )
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with opener.open(control_url, timeout=3) as response:
                        control = json.loads(response.read().decode("utf-8"))
                    if control["finalize_requested"]:
                        post(
                            endpoint_holder[0],
                            {
                                "protocol_version": 2,
                                "run_id": run_id,
                                "session_id": "browser-v2",
                                "events": [],
                                "final": True,
                                "final_seq": 1,
                            },
                        )
                        return
                    time.sleep(0.01)

            worker = threading.Thread(target=finalize_when_requested, daemon=True)
            worker_holder.append(worker)
            worker.start()

        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory,
                use_network_probe=False,
                trace_finalize_timeout_seconds=2,
                adapter_factory={
                    "manual": lambda: ManualAdapter(
                        opener=visit,
                        completion_waiter=start_finalizer,
                    )
                },
            )
            result = runner.run_once(
                "/mouse-click.html",
                "interact manually",
                agent_name="manual",
                run_id=run_id,
            )
            worker_holder[0].join(timeout=2)
            self.assertTrue(result.success, result.manifest)
            dynamic = json.loads(
                (result.output_dir / "fingerprints" / "l3_browser_dynamic.json").read_text(encoding="utf-8")
            )
            self.assertEqual(dynamic["event_count"], 1)

    def test_pipeline_reports_unconfirmed_protocol_v2_session(self) -> None:
        run_id = "manual-final-timeout"

        def visit_without_final_ack(url: str) -> bool:
            endpoint = url.rsplit("/", 1)[0] + "/__agent_fingerprint__/events"
            request = Request(
                endpoint,
                data=json.dumps(
                    {
                        "protocol_version": 2,
                        "run_id": run_id,
                        "session_id": "unresponsive-browser",
                        "events": [{"seq": 1, "type": "monitor_start", "epoch_ms": 1}],
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            return True

        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory,
                use_network_probe=False,
                trace_finalize_timeout_seconds=0.05,
                adapter_factory={
                    "manual": lambda: ManualAdapter(
                        opener=visit_without_final_ack,
                        completion_waiter=lambda prompt: None,
                    )
                },
            )
            result = runner.run_once(
                "/mouse-click.html",
                "interact manually",
                agent_name="manual",
                run_id=run_id,
            )
            self.assertFalse(result.success)
            self.assertIn("browser trace finalization timed out", result.manifest["error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
