"""Offline complete-cycle tests; no browser, Conda, tcpdump, or LLM needed."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from adapters.base_adapter import AgentInterruptedError, AgentResult
from gateway.mock_openai import MockOpenAIGateway
from pipeline import PipelineRunner
from gateway.litellm_gateway import GatewayConfig


class FakeAdapter:
    def run_task(self, url, prompt, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text(json.dumps({"url": url, "prompt": prompt}), encoding="utf-8")
        return AgentResult("fake", ("fake-agent",), output_dir, 0, "done", "", 0.01, task_success=True)


class FakeCapture:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_path = self.output_dir / "traffic.pcap"
        self.stopped = False

    def start(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path.write_bytes(b"FAKEPCAP")
        return self

    def stop(self):
        self.stopped = True
        from probes.traffic_sniffer import CaptureResult

        return CaptureResult(
            "tcpdump",
            ("fake",),
            self.output_path,
            self.output_dir,
            "now",
            "now",
            0,
            0.01,
            self.output_dir / "out",
            self.output_dir / "err",
        )


class PipelineTest(unittest.TestCase):
    def test_interaction_delay_is_validated(self):
        with self.assertRaisesRegex(ValueError, "interaction_delay_seconds"):
            PipelineRunner(use_network_probe=False, interaction_delay_seconds=-0.1)

    def test_trace_finalize_timeout_is_validated(self):
        with self.assertRaisesRegex(ValueError, "trace_finalize_timeout_seconds"):
            PipelineRunner(use_network_probe=False, trace_finalize_timeout_seconds=0)

    def test_autogen_resolves_capability_model_from_gateway_route(self):
        config = GatewayConfig(
            "/tmp/autogen-route-test.yaml",
            {
                "model_list": [
                    {
                        "model_name": "chat-gpt",
                        "litellm_params": {"model": "openai/gpt-5.6-sol"},
                    }
                ]
            },
        )
        gateway = SimpleNamespace(config=config, base_url="http://127.0.0.1:4000/v1", auth_key="sk-test")
        runner = PipelineRunner(use_network_probe=False, adapter_options={"autogen": {"conda_env": "autogen"}})
        adapter = runner._make_adapter("autogen", model="chat-gpt", gateway=gateway)
        self.assertEqual(adapter.llm_model, "chat-gpt")
        self.assertEqual(adapter.upstream_model, "openai/gpt-5.6-sol")

    def test_skyvern_receives_litellm_gateway_configuration(self):
        gateway = SimpleNamespace(base_url="http://127.0.0.1:4000/v1", auth_key="sk-test")
        runner = PipelineRunner(use_network_probe=False)
        adapter = runner._make_adapter("skyvern", model="chat-gpt", gateway=gateway)
        self.assertEqual(adapter.llm_model, "chat-gpt")
        self.assertEqual(adapter.llm_api_base, "http://127.0.0.1:4000/v1")
        self.assertEqual(adapter.llm_api_key, "sk-test")

    def test_agente_receives_litellm_gateway_configuration(self):
        gateway = SimpleNamespace(base_url="http://127.0.0.1:4000/v1", auth_key="sk-test")
        runner = PipelineRunner(
            use_network_probe=False,
            adapter_options={"agente": {"endpoint_url": "http://127.0.0.1:8080/execute_task"}},
        )
        adapter = runner._make_adapter("agente", model="deepseek-chat", gateway=gateway)
        self.assertEqual(adapter.llm_model, "deepseek-chat")
        self.assertEqual(adapter.llm_api_base, "http://127.0.0.1:4000/v1")
        self.assertEqual(adapter.llm_api_key, "sk-test")

    def test_model_and_route_alias_cannot_diverge(self):
        runner = PipelineRunner(output_root=tempfile.mkdtemp(), use_network_probe=False)
        with self.assertRaisesRegex(ValueError, "model and route_alias"):
            runner.run_once(
                "/01-minimal.html",
                "click",
                agent_name="mock",
                model="public-a",
                route_alias="public-b",
            )

    def test_custom_gateway_must_apply_requested_route(self):
        class GatewayWithoutRouting:
            pass

        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory,
                use_network_probe=False,
                gateway_factory=lambda run_dir: GatewayWithoutRouting(),
                adapter_factory={"fake": FakeAdapter},
            )
            result = runner.run_once(
                "/01-minimal.html",
                "click",
                agent_name="fake",
                upstream_model="openai/test-model",
            )
            self.assertFalse(result.success)
            self.assertIn("dynamic route switching", result.manifest["error"])

    def test_route_switch_isolated_per_run(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.yaml"
            source = GatewayConfig(
                source_path,
                {
                    "model_list": [
                        {
                            "model_name": "alias",
                            "litellm_params": {
                                "model": "openai/original",
                                "api_key": "os.environ/RELAY_OPENAI_API_KEY",
                            },
                        }
                    ]
                },
            )
            runner = PipelineRunner(
                output_root=Path(directory) / "runs", gateway_config=source, use_network_probe=False
            )
            run_dir = Path(directory) / "runs" / "r1"
            run_dir.mkdir(parents=True)
            gateway = runner._make_gateway(run_dir)
            route = runner._configure_gateway_route(
                gateway,
                agent_name="webvoyager",
                model="alias",
                upstream_model="anthropic/claude-test",
                provider_key_env="ANTHROPIC_API_KEY",
                route_alias="alias",
            )
            self.assertEqual(route["alias"], "alias")
            self.assertEqual(gateway.config.routes["alias"].model, "anthropic/claude-test")
            self.assertEqual(gateway.config.routes["alias"].api_key_env, "ANTHROPIC_API_KEY")
            self.assertEqual(source.routes["alias"].model, "openai/original")

    def test_factory_can_mix_positional_path_and_keyword_context(self):
        captured = {}

        def factory(path, *, model=None):
            captured["path"] = path
            captured["model"] = model
            return object()

        runner = PipelineRunner(adapter_factory={"custom": factory}, use_network_probe=False)
        value = runner._make_adapter("custom", model="alias", run_dir=Path("/tmp/run"))
        self.assertIsNotNone(value)
        self.assertEqual(captured["path"], Path("/tmp/run") / "agent")
        self.assertEqual(captured["model"], "alias")

    def test_run_id_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory, use_network_probe=False, adapter_factory={"fake": FakeAdapter}
            )
            with self.assertRaises(ValueError):
                runner.run_once("/01-minimal.html", "click", agent_name="fake", run_id="../escape")

    def test_external_sandbox_is_not_stopped_by_one_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory, use_network_probe=False, adapter_factory={"fake": FakeAdapter}
            )
            handle = runner.start_sandbox_server("external-owner")
            result = runner.run_once(
                handle.base_url + "/01-minimal.html",
                "click confirm",
                agent_name="fake",
                run_id="external-owner-run",
            )
            self.assertTrue(result.success)
            self.assertIsNotNone(runner._sandbox)
            runner.stop_sandbox_server()

    def test_complete_offline_cycle_writes_manifest_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory,
                gateway_config=None,
                sniffer_factory=FakeCapture,
                adapter_factory={"fake": FakeAdapter},
            )
            result = runner.run_once(
                "/01-minimal.html",
                "click confirm",
                agent_name="fake",
                run_id="cycle-001",
            )
            self.assertTrue(result.success)
            self.assertTrue((result.output_dir / "manifest.json").is_file())
            self.assertTrue((result.output_dir / "fingerprints" / "l3_browser_dynamic.json").is_file())
            self.assertTrue((result.output_dir / "artifacts" / "network" / "traffic.pcap").is_file())
            self.assertFalse((result.output_dir / "ui_trace.json").exists())
            self.assertFalse((result.output_dir / "result.json").exists())
            self.assertFalse((result.output_dir / "logs" / "manifest.json").exists())
            manifest = json.loads((result.output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["schema"], "agent-fingerprint-run/v2")
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["run_id"], "cycle-001")
            self.assertEqual(manifest["task"]["url"].split("/")[-1], "01-minimal.html")
            self.assertTrue((Path(directory) / "index.jsonl").is_file())

    def test_skyvern_media_is_archived_outside_logs(self):
        class FakeSkyvernAdapter:
            def run_task(self, url, prompt, output_dir):
                output = Path(output_dir)
                recordings = output / "skyvern_artifacts" / "recordings"
                screenshots = output / "skyvern_artifacts" / "screenshots"
                recordings.mkdir(parents=True)
                screenshots.mkdir(parents=True)
                recording = recordings / "temporary-recording.mp4"
                screenshot = screenshots / "temporary-step.png"
                recording.write_bytes(b"video")
                screenshot.write_bytes(b"image")
                (output / "result.json").write_text(
                    json.dumps(
                        {
                            "output": "done",
                            "task_success": True,
                            "task_status": "completed",
                            "recording_url": recording.as_uri(),
                            "screenshot_urls": [screenshot.as_uri()],
                        }
                    ),
                    encoding="utf-8",
                )
                return AgentResult("skyvern", ("python", "-c", "inline"), output, 0, "done", "", 0.01, task_success=True)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            runner = PipelineRunner(
                use_network_probe=False,
                adapter_factory={"skyvern-test": FakeSkyvernAdapter},
            )
            result = runner.run_once(
                "/01-minimal.html",
                "test",
                agent_name="skyvern-test",
                run_id="skyvern-media",
                output_dir=output,
            )
            self.assertTrue(result.success, result.manifest)
            self.assertEqual(
                (output / "artifacts" / "recordings" / "recording.mp4").read_bytes(),
                b"video",
            )
            self.assertEqual(
                (output / "artifacts" / "screenshots" / "step.png").read_bytes(),
                b"image",
            )
            self.assertFalse((output / "logs" / "agent").exists())
            self.assertEqual(
                result.manifest["artifacts"]["recordings"],
                ["artifacts/recordings/recording.mp4"],
            )

    def test_matrix_uses_unique_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory, use_network_probe=False, adapter_factory={"fake": FakeAdapter}
            )
            results = runner.run_matrix(
                [{"url": "/01-minimal.html", "prompt": "one"}, {"url": "/03-flat.html", "prompt": "two"}],
                ["fake"],
            )
            self.assertEqual(len(results), 2)
            self.assertNotEqual(results[0].run_id, results[1].run_id)

    def test_explicit_overwrite_clears_managed_artifacts_only(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixed"
            runner = PipelineRunner(
                output_root=Path(directory) / "runs",
                use_network_probe=False,
                adapter_factory={"fake": FakeAdapter},
            )
            first = runner.run_once("/01-minimal.html", "one", agent_name="fake", output_dir=output)
            (output / "gateway_events.jsonl").write_text("stale\n", encoding="utf-8")
            (output / "keep.txt").write_text("caller-owned", encoding="utf-8")
            second = runner.run_once("/01-minimal.html", "two", agent_name="fake", output_dir=output, overwrite=True)
            self.assertTrue(first.success)
            self.assertTrue(second.success)
            self.assertFalse((output / "gateway_events.jsonl").exists())
            self.assertEqual((output / "keep.txt").read_text(encoding="utf-8"), "caller-owned")

    def test_explicit_output_dir_preserves_run_id_for_ui_trace(self):
        """The trace namespace follows run_id, even when directory names differ."""

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "human-readable-output"
            (Path(directory) / "01-minimal.html").write_text("<!doctype html><body>Fixture</body>")

            def make_gateway(run_dir):
                return MockOpenAIGateway(run_dir / "mock_gateway")

            runner = PipelineRunner(
                output_root=Path(directory) / "runs",
                sandbox_directory=directory,
                gateway_factory=make_gateway,
                use_network_probe=False,
            )
            result = runner.run_once(
                "/01-minimal.html",
                "emit one event",
                agent_name="mock",
                run_id="stable-run-id",
                output_dir=output,
            )
            trace = json.loads(
                (output / "fingerprints" / "l3_browser_dynamic.json").read_text(encoding="utf-8")
            )
            self.assertTrue(result.success, result.manifest)
            self.assertEqual(result.run_id, "stable-run-id")
            self.assertEqual(trace["run_id"], "stable-run-id")
            self.assertEqual(trace["event_count"], 1)
            self.assertIn("manifest.json", result.manifest["files"])

    def test_interrupt_writes_manifest_and_does_not_continue(self):
        class InterruptAdapter:
            def run_task(self, url, prompt, output_dir):
                output_dir = Path(output_dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                result = AgentResult("interrupt", (), output_dir, -2, "", "", 0.01)
                raise AgentInterruptedError("operator stopped task", result)

        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(
                output_root=directory,
                use_network_probe=False,
                adapter_factory={"interrupt": InterruptAdapter},
            )
            with self.assertRaises(KeyboardInterrupt):
                runner.run_once("/01-minimal.html", "stop", agent_name="interrupt", run_id="interrupted")
            manifest_path = next(Path(directory).glob("*/interrupted/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("operator stopped task", manifest["error"])
            self.assertIsNone(runner._sandbox)


if __name__ == "__main__":
    unittest.main()
