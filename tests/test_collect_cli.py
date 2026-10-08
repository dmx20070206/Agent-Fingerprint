"""Tests for the collection CLI without launching a real Agent or proxy."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fingerprint.cli.collect import _build_gateway_factory, _build_parser, _build_runner, _load_tasks


class CollectionCliTest(unittest.TestCase):
    def test_task_file_loader_accepts_webvoyager_field_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.jsonl"
            path.write_text('{"web":"/01-minimal.html","ques":"click"}\n', encoding="utf-8")
            self.assertEqual(
                _load_tasks(path),
                [{"web": "/01-minimal.html", "ques": "click", "url": "/01-minimal.html", "prompt": "click"}],
            )

    def test_cli_parser_has_full_cycle_flags(self) -> None:
        args = _build_parser().parse_args(
            [
                "--task-file", "tasks.json",
                "--mock-llm",
                "--no-network-probe",
                "--capture-filter",
                "host 127.0.0.1",
            ]
        )
        self.assertTrue(args.mock_llm)
        self.assertTrue(args.no_network_probe)
        self.assertEqual(args.capture_filter, "host 127.0.0.1")

    def test_litellm_master_key_is_checked_before_startup(self) -> None:
        args = _build_parser().parse_args([])
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "local-key"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "sk-"):
                _build_gateway_factory(args)

    def test_conda_environments_come_from_config_as_names(self) -> None:
        args = _build_parser().parse_args(["--task-file", "tasks.json", "--mock-llm"])
        runner = _build_runner(args)
        self.assertEqual(runner.adapter_options["webvoyager"]["conda_env"], "webvoyager")
        self.assertEqual(runner.adapter_options["browseruse"]["conda_env"], "browser-use")
        self.assertEqual(runner.adapter_options["skyvern"]["conda_env"], "skyvern")
        self.assertEqual(runner.adapter_options["agente"]["conda_env"], "agent-e")

    def test_agente_endpoint_reaches_adapter_options(self) -> None:
        args = _build_parser().parse_args(
            [
                "--agent", "agente", "--mock-llm", "--max-steps", "50",
                "--agente-endpoint", "http://127.0.0.1:8080/execute_task",
            ]
        )
        runner = _build_runner(args)
        self.assertEqual(
            runner.adapter_options["agente"]["endpoint_url"],
            "http://127.0.0.1:8080/execute_task",
        )
        self.assertEqual(runner.adapter_options["agente"]["max_steps"], 50)

    def test_skyvern_can_disable_project_gateway(self) -> None:
        args = _build_parser().parse_args(["--agent", "skyvern", "--no-gateway"])
        self.assertIsNone(_build_gateway_factory(args))

    def test_manual_mode_needs_no_gateway_or_task_file(self) -> None:
        args = _build_parser().parse_args(
            ["--agent", "manual", "--url", "/mouse-click.html", "--no-network-probe"]
        )
        self.assertIsNone(_build_gateway_factory(args))
        runner = _build_runner(args)
        self.assertTrue(runner.adapter_options["manual"]["open_browser"])
        self.assertFalse(runner.use_network_probe)
        self.assertEqual(runner.trace_grace_seconds, 0.5)
        self.assertEqual(runner.trace_finalize_timeout_seconds, 8.0)
        self.assertEqual(runner.interaction_delay_seconds, 0.0)

    def test_autogen_vision_override_reaches_adapter_options(self) -> None:
        args = _build_parser().parse_args(
            ["--task-file", "tasks.json", "--mock-llm", "--autogen-vision", "false"]
        )
        runner = _build_runner(args)
        self.assertIs(runner.adapter_options["autogen"]["vision"], False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
