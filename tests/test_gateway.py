"""Unit tests for LiteLLM gateway configuration and process commands."""

from __future__ import annotations

import tempfile
import unittest
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

from agent_fingerprint.collection.gateway.litellm_gateway import GatewayConfig, GatewayError, LiteLLMGateway
from agent_fingerprint.collection.gateway.latency_callback import AgentFingerprintCallback


class GatewayConfigTest(unittest.TestCase):
    def test_routes_are_loaded_and_written_without_literal_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                """
model_list:
  - model_name: chat-gpt
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/RELAY_OPENAI_API_KEY
""",
                encoding="utf-8",
            )
            config = GatewayConfig.load(path)
            config.set_route(
                "claude-alias",
                "anthropic/claude-sonnet-4-6",
                api_key_env="ANTHROPIC_API_KEY",
            )
            config.set_master_key()
            config.write()
            written = path.read_text(encoding="utf-8")
            self.assertIn("claude-alias", written)
            self.assertIn("os.environ/ANTHROPIC_API_KEY", written)
            self.assertNotIn("sk-", written)

    def test_literal_provider_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GatewayConfig(
                "config.yaml",
                {
                    "model_list": [
                        {
                            "model_name": "bad",
                            "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-secret"},
                        }
                    ]
                },
            )

    def test_invalid_environment_reference_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GatewayConfig(
                "config.yaml",
                {
                    "model_list": [
                        {
                            "model_name": "bad",
                            "litellm_params": {
                                "model": "openai/gpt-4o",
                                "api_key": "os.environ/NOT-A-NAME",
                            },
                        }
                    ]
                },
            )


class GatewayTest(unittest.TestCase):
    def test_builtin_callback_is_materialized_next_to_isolated_config(self) -> None:
        """LiteLLM versions that resolve callbacks from config_dir can import it."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = GatewayConfig(
                root / "litellm.config.yaml",
                {
                    "model_list": [],
                    "litellm_settings": {
                        "callbacks": "gateway.latency_callback.proxy_handler_instance",
                    },
                },
            )
            gateway = LiteLLMGateway(config, output_dir=root)
            gateway._materialize_builtin_callbacks()
            copied = root / "gateway" / "latency_callback.py"
            self.assertTrue(copied.is_file())
            self.assertEqual(
                copied.read_bytes(),
                Path("src/agent_fingerprint/collection/gateway/latency_callback.py").read_bytes(),
            )
            self.assertTrue((root / "gateway" / "__init__.py").is_file())

    def test_start_isolates_source_config_when_output_dir_differs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "configs" / "litellm.yaml"
            source.parent.mkdir()
            source.write_text(
                "model_list: []\n" "general_settings: {}\n" "litellm_settings: {}\n",
                encoding="utf-8",
            )
            gateway = LiteLLMGateway(source, output_dir=root / "run")
            # Avoid spawning LiteLLM: the assertion is about the private
            # config/callback paths selected before process startup.
            with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "sk-test"}, clear=False), patch.object(
                gateway, "wait_ready"
            ):
                with patch("agent_fingerprint.collection.gateway.litellm_gateway.subprocess.Popen") as popen:
                    process = popen.return_value
                    process.poll.return_value = 0
                    process.pid = 12345
                    process.returncode = 0
                    gateway.start()
            try:
                self.assertEqual(gateway.config.path.parent, (root / "run").resolve())
                self.assertTrue(gateway.config.path.is_file())
                self.assertTrue((root / "run" / "gateway" / "latency_callback.py").is_file())
                # The checked-in/source config remains free of generated
                # master-key/callback edits.
                source_text = source.read_text(encoding="utf-8")
                self.assertNotIn("callbacks", source_text)
                self.assertNotIn("master_key", source_text)
            finally:
                gateway.stop()

    def test_command_and_urls(self) -> None:
        config = GatewayConfig("/tmp/litellm.yaml", {"model_list": []})
        gateway = LiteLLMGateway(config, host="127.0.0.1", port=4567)
        self.assertEqual(
            gateway.command(),
            ["litellm", "--config", "/tmp/litellm.yaml", "--host", "127.0.0.1", "--port", "4567"],
        )
        self.assertEqual(gateway.base_url, "http://127.0.0.1:4567/v1")
        self.assertEqual(gateway.health_url, "http://127.0.0.1:4567/health/liveliness")

    def test_port_collision_is_detected_before_proxy_spawn(self) -> None:
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        try:
            port = occupied.getsockname()[1]
            gateway = LiteLLMGateway(
                GatewayConfig("/tmp/litellm.yaml", {"model_list": []}),
                host="127.0.0.1",
                port=port,
            )
            with self.assertRaisesRegex(GatewayError, "already in use"):
                gateway._assert_port_available()
        finally:
            occupied.close()

    def test_start_requires_master_key_before_spawning_process(self) -> None:
        config = GatewayConfig("/tmp/litellm.yaml", {"model_list": []})
        gateway = LiteLLMGateway(config, output_dir=tempfile.mkdtemp())
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": ""}, clear=False):
            with self.assertRaisesRegex(Exception, "master key"):
                gateway.start()
        self.assertIsNone(gateway.process)

    def test_metadata_failure_stops_started_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = GatewayConfig(Path(directory) / "config.yaml", {"model_list": []})
            gateway = LiteLLMGateway(config, output_dir=directory)

            class RunningProcess:
                pid = 12345
                returncode = 0

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0

            with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "sk-test"}, clear=False), patch(
                "agent_fingerprint.collection.gateway.litellm_gateway.subprocess.Popen",
                return_value=RunningProcess(),
            ), patch.object(gateway, "wait_ready"), patch.object(
                gateway, "_write_runtime_metadata", side_effect=OSError("disk full")
            ):
                with self.assertRaises(OSError):
                    gateway.start()
            self.assertIsNone(gateway.process)

    @patch.object(LiteLLMGateway, "start")
    @patch.object(LiteLLMGateway, "stop")
    def test_switch_route_rewrites_config_and_restarts(self, stop, start) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            config = GatewayConfig(path, {"model_list": []})
            gateway = LiteLLMGateway(config)
            gateway.process = object()  # simulate a running child

            # ``poll`` is only used by switch_route; use a tiny mock process.
            class RunningProcess:
                def poll(self):
                    return None

            gateway.process = RunningProcess()
            gateway.switch_route(
                "agent-model",
                "anthropic/claude-sonnet-4-6",
                api_key_env="ANTHROPIC_API_KEY",
            )
            self.assertIn("agent-model", config.routes)
            self.assertTrue(path.is_file())
            stop.assert_called_once()
            start.assert_called_once_with(write_config=False)

    def test_callback_uses_stable_id_and_deduplicates_terminal_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            callback = AgentFingerprintCallback()
            old = os.environ.get("AGENT_FINGERPRINT_GATEWAY_LOG")
            os.environ["AGENT_FINGERPRINT_GATEWAY_LOG"] = str(path)
            try:
                kwargs = {"model": "mock", "request_id": "req-1", "messages": [{"content": "secret"}]}
                callback.log_pre_api_call("mock", kwargs["messages"], kwargs)
                copied = {
                    key: value for key, value in kwargs.items() if key not in {"_agent_fp_request_id", "request_id"}
                }
                callback.log_post_api_call(copied, {"usage": {"total_tokens": 2}}, None, None)
                callback.log_success_event(copied, {"usage": {"total_tokens": 2}}, None, None)
            finally:
                if old is None:
                    os.environ.pop("AGENT_FINGERPRINT_GATEWAY_LOG", None)
                else:
                    os.environ["AGENT_FINGERPRINT_GATEWAY_LOG"] = old
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["event"] for record in records], ["request_start", "request_end"])
            self.assertEqual(records[0]["request_id"], records[1]["request_id"])
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))

    def test_callback_failure_records_shape_without_request_or_response_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            callback = AgentFingerprintCallback()
            old = os.environ.get("AGENT_FINGERPRINT_GATEWAY_LOG")
            os.environ["AGENT_FINGERPRINT_GATEWAY_LOG"] = str(path)
            try:
                kwargs = {
                    "model": "mock",
                    "request_id": "failure-redaction-req-1",
                    "messages": [{"role": "user", "content": "private prompt"}],
                }
                callback.log_pre_api_call("mock", kwargs["messages"], kwargs)
                callback.log_failure_event(
                    kwargs,
                    {"error": {"message": "private response"}},
                    None,
                    None,
                )
            finally:
                if old is None:
                    os.environ.pop("AGENT_FINGERPRINT_GATEWAY_LOG", None)
                else:
                    os.environ["AGENT_FINGERPRINT_GATEWAY_LOG"] = old
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["event"] for record in records], ["request_start", "request_error"])
            logged = path.read_text(encoding="utf-8")
            self.assertNotIn("private prompt", logged)
            self.assertNotIn("private response", logged)

    def test_callback_keeps_two_rapid_requests_when_terminal_kwargs_are_copied(self) -> None:
        """A missing private id must not collapse concurrent completions."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rapid-events.jsonl"
            callback = AgentFingerprintCallback()
            old = os.environ.get("AGENT_FINGERPRINT_GATEWAY_LOG")
            os.environ["AGENT_FINGERPRINT_GATEWAY_LOG"] = str(path)
            try:
                model = "rapid-model-for-regression"
                first = {"model": model, "messages": []}
                second = {"model": model, "messages": []}
                callback.log_pre_api_call(model, first["messages"], first)
                callback.log_pre_api_call(model, second["messages"], second)
                # Simulate LiteLLM passing a copied mapping that lost both the
                # public request id and our private marker.
                callback.log_post_api_call({"model": model}, {"usage": {}}, None, None)
                callback.log_post_api_call({"model": model}, {"usage": {}}, None, None)
            finally:
                if old is None:
                    os.environ.pop("AGENT_FINGERPRINT_GATEWAY_LOG", None)
                else:
                    os.environ["AGENT_FINGERPRINT_GATEWAY_LOG"] = old
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [record["event"] for record in records],
                ["request_start", "request_start", "request_end", "request_end"],
            )
            self.assertEqual(len({record["request_id"] for record in records[2:]}), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
