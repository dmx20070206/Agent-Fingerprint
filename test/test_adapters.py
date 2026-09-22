"""Unit tests for adapter command construction.

These tests deliberately mock child-process execution.  Running a real Agent
would require Conda environments, browser binaries, and an LLM API key.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters.base_adapter import AgentExecutionError, AgentResult, BaseAgentAdapter
from adapters.agente_adapter import AgentEAdapter
from adapters.browseruse_adapter import BrowserUseAdapter, _RUNNER as BROWSERUSE_RUNNER
from adapters.webvoyager_adapter import WebVoyagerAdapter, _has_matching_selenium_cache
from adapters.skyvern_adapter import SkyvernAdapter, _RUNNER as SKYVERN_RUNNER
from adapters.autogen_adapter import AutoGenAdapter, _RUNNER as AUTOGEN_RUNNER


class _FakeAdapter(BaseAgentAdapter):
    adapter_name = "fake"

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        path = self.prepare_output_dir(output_dir)
        return self.execute(
            self.conda_command(["-c", "print('ok')"]),
            output_dir=path,
        )


class AdapterTest(unittest.TestCase):
    def test_browseruse_keeps_native_click_but_blocks_dom_evaluation(self) -> None:
        self.assertIn('Tools(exclude_actions=["evaluate"])', BROWSERUSE_RUNNER)
        self.assertNotIn('Tools(exclude_actions=["evaluate", "click"])', BROWSERUSE_RUNNER)
        self.assertIn("For ordinary form interactions, use browser-use's native click action", BROWSERUSE_RUNNER)

    def test_autogen_web_surfer_limits_actual_turns(self) -> None:
        self.assertIn("max_turns=max_turns", AUTOGEN_RUNNER)
        self.assertNotIn("MaxMessageTermination(max_messages=", AUTOGEN_RUNNER)

    def test_autogen_web_surfer_reads_multimodal_success_text(self) -> None:
        self.assertIn("def _content_text(content):", AUTOGEN_RUNNER)
        self.assertIn('"successfully been booked"', AUTOGEN_RUNNER)
        self.assertIn('TextMentionTermination("successfully booked"', AUTOGEN_RUNNER)
        self.assertIn('getattr(msg, "source", None) == agent.name', AUTOGEN_RUNNER)

    def test_autogen_web_surfer_supports_native_select_controls(self) -> None:
        self.assertIn('name="select_option"', AUTOGEN_RUNNER)
        self.assertIn("class FormWebSurfer(MultimodalWebSurfer):", AUTOGEN_RUNNER)
        self.assertIn("await target.select_option(value=option_value)", AUTOGEN_RUNNER)

    def test_autogen_normalizes_assistant_ending_history_for_gemini(self) -> None:
        self.assertIn("def _gemini_compatible_messages(messages):", AUTOGEN_RUNNER)
        self.assertIn(
            "normalized[-2], normalized[-1] = normalized[-1], normalized[-2]",
            AUTOGEN_RUNNER,
        )
        self.assertIn(
            "class GeminiCompatibleOpenAIChatCompletionClient(OpenAIChatCompletionClient):",
            AUTOGEN_RUNNER,
        )

    def test_autogen_gemini_history_conversion_is_model_specific(self) -> None:
        self.assertIn("if _is_gemini_model(capability_model or client_kwargs", AUTOGEN_RUNNER)
        self.assertIn("else OpenAIChatCompletionClient", AUTOGEN_RUNNER)

    def test_autogen_text_models_receive_browser_tools(self) -> None:
        self.assertIn("class TextBrowser:", AUTOGEN_RUNNER)
        self.assertIn("async def _run_text_browser(", AUTOGEN_RUNNER)
        self.assertIn("tools=[observe, click, type_text, select_option, hover, scroll, press, go_back, wait]", AUTOGEN_RUNNER)
        self.assertIn("Initial browser observation:", AUTOGEN_RUNNER)
        self.assertNotIn("result_text, task_success, task_status = await _run_assistant", AUTOGEN_RUNNER)

    def test_autogen_text_browser_does_not_send_images(self) -> None:
        self.assertIn("_make_client(cfg, vision=False, capability_model=capability_model)", AUTOGEN_RUNNER)
        self.assertIn("document.querySelectorAll(selector)", AUTOGEN_RUNNER)

    @patch.object(AutoGenAdapter, "execute")
    def test_autogen_uses_upstream_model_for_vision_detection(self, execute) -> None:
        execute.return_value = AgentResult("autogen", (), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            AutoGenAdapter(
                conda_env="autogen", llm_model="chat-gpt",
                upstream_model="openai/gpt-5.6-sol",
            ).run_task("http://127.0.0.1:8000/index.html", "book flight", directory)
        environment = execute.call_args.kwargs["env"]
        self.assertEqual(environment["AUTOGEN_LLM_MODEL"], "chat-gpt")
        self.assertEqual(environment["AUTOGEN_UPSTREAM_MODEL"], "openai/gpt-5.6-sol")

    @patch.object(AutoGenAdapter, "execute")
    def test_autogen_explicit_vision_override_is_forwarded(self, execute) -> None:
        execute.return_value = AgentResult("autogen", (), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            AutoGenAdapter(conda_env="autogen", vision=False).run_task(
                "http://127.0.0.1:8000/index.html", "inspect", directory
            )
        self.assertEqual(execute.call_args.kwargs["env"]["AUTOGEN_VISION"], "0")

    def test_agente_requires_explicit_http_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception) as error:
                AgentEAdapter(conda_env=None).run_task(
                    "http://127.0.0.1:8000/01-minimal.html", "click", directory
                )
            self.assertIn("no stable batch CLI", str(error.exception))

    @patch.object(AgentEAdapter, "execute")
    def test_agente_forwards_gateway_model_config_to_service(self, execute) -> None:
        execute.return_value = AgentResult("agente", (), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            AgentEAdapter(
                conda_env="agent-e",
                endpoint_url="http://127.0.0.1:8080/execute_task",
                llm_model="deepseek-chat",
                llm_api_base="http://127.0.0.1:4000/v1/",
                llm_api_key="sk-gateway",
                max_steps=50,
            ).run_task("http://127.0.0.1:8000/mouse-click.html", "click", directory)
        environment = execute.call_args.kwargs["env"]
        self.assertEqual(environment["AGENTE_LLM_MODEL"], "deepseek-chat")
        self.assertEqual(environment["AGENTE_LLM_API_BASE"], "http://127.0.0.1:4000/v1")
        self.assertEqual(environment["AGENTE_LLM_API_KEY"], "sk-gateway")
        self.assertEqual(environment["AGENTE_MAX_STEPS"], "50")
        self.assertEqual(execute.call_args.kwargs["redact_values"], ("sk-gateway",))
        self.assertIn('payload_data["llm_config"]', execute.call_args.kwargs["input_text"])
        self.assertIn('payload_data["planner_max_chat_round"]', execute.call_args.kwargs["input_text"])
        self.assertIn('"max_turns_reached": "max_turns_reached"', execute.call_args.kwargs["input_text"])

    def test_conda_name_and_prefix_commands(self) -> None:
        named = _FakeAdapter(conda_env="demo")
        self.assertEqual(
            named.conda_command(["script.py"]),
            ["conda", "run", "--no-capture-output", "--name", "demo", "python", "script.py"],
        )
        prefixed = _FakeAdapter(conda_env=None, conda_env_path="/tmp/demo")
        command = prefixed.conda_command(["script.py"])
        self.assertEqual(command[:5], ["conda", "run", "--no-capture-output", "--prefix", "/tmp/demo"])
        auto_prefixed = _FakeAdapter(conda_env="/tmp/demo")
        self.assertEqual(auto_prefixed.conda_command(["script.py"]), command)

    def test_framework_adapters_accept_an_absolute_conda_prefix(self) -> None:
        browser = BrowserUseAdapter(conda_env_path="/opt/conda/envs/browser-use")
        voyager = WebVoyagerAdapter(conda_env_path="/opt/conda/envs/webvoyager")
        skyvern = SkyvernAdapter(conda_env_path="/opt/conda/envs/skyvern")
        self.assertIn("--prefix", browser.conda_command(["-"]))
        self.assertIn("--prefix", voyager.conda_command(["run.py"]))
        self.assertIn("--prefix", skyvern.conda_command(["-"]))

    @patch.object(SkyvernAdapter, "execute")
    def test_skyvern_uses_sdk_child_and_keeps_key_out_of_argv(self, execute) -> None:
        execute.return_value = AgentResult("skyvern", (), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            SkyvernAdapter(
                conda_env="skyvern", api_key="secret-key",
                base_url="http://127.0.0.1:8000", max_steps=7,
            ).run_task("http://example.test", "complete task", directory)
        command = execute.call_args.args[0]
        environment = execute.call_args.kwargs["env"]
        self.assertNotIn("secret-key", command)
        self.assertEqual(environment["SKYVERN_API_KEY"], "secret-key")
        self.assertEqual(environment["SKYVERN_MAX_STEPS"], "7")
        self.assertIn("wait_for_completion", command[-1])

    @patch.object(SkyvernAdapter, "execute")
    def test_skyvern_embedded_mode_targets_litellm_gateway(self, execute) -> None:
        execute.return_value = AgentResult("skyvern", (), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            SkyvernAdapter(
                conda_env="skyvern",
                llm_model="chat-gpt",
                llm_api_base="http://127.0.0.1:4000/v1/",
                llm_api_key="sk-gateway",
            ).run_task("http://example.test", "complete task", directory)
        command = execute.call_args.args[0]
        environment = execute.call_args.kwargs["env"]
        self.assertEqual(environment["SKYVERN_LLM_MODEL"], "chat-gpt")
        self.assertEqual(environment["SKYVERN_LLM_API_BASE"], "http://127.0.0.1:4000/v1")
        self.assertEqual(environment["SKYVERN_LLM_API_KEY"], "sk-gateway")
        self.assertEqual(environment["ALLOWED_HOSTS"], '["example.test"]')
        self.assertEqual(environment["BROWSER_TYPE"], "chromium-headless")
        self.assertEqual(execute.call_args.kwargs["redact_values"], ("sk-gateway",))
        self.assertIn("Skyvern.local", command[-1])
        self.assertIn("LLMConfig", command[-1])
        self.assertIn("await client.aclose()", command[-1])

    def test_skyvern_persists_local_recording_and_screenshots_before_close(self) -> None:
        fake_skyvern = '''
import shutil
import tempfile
from pathlib import Path

class Result:
    run_id = "tsk_test"
    status = "completed"
    output = None
    failure_reason = None
    step_count = 2

class Skyvern:
    def __init__(self, **kwargs):
        self.artifact_dir = None

    async def run_task(self, **kwargs):
        self.artifact_dir = Path(tempfile.mkdtemp(prefix="fake-skyvern-artifacts-"))
        recording = self.artifact_dir / "recording.mp4"
        screenshot_one = self.artifact_dir / "action-1.png"
        screenshot_two = self.artifact_dir / "action-2.png"
        recording.write_bytes(b"video")
        screenshot_one.write_bytes(b"image-one")
        screenshot_two.write_bytes(b"image-two")
        result = Result()
        result.recording_url = recording.as_uri()
        result.screenshot_urls = [screenshot_one.as_uri(), screenshot_two.as_uri()]
        return result

    async def aclose(self):
        shutil.rmtree(self.artifact_dir)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module_dir = root / "fake_module"
            output_dir = root / "logs" / "agent"
            module_dir.mkdir()
            output_dir.mkdir(parents=True)
            (module_dir / "skyvern.py").write_text(fake_skyvern, encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "AGENT_OUTPUT_DIR": str(output_dir),
                    "AGENT_TASK_PROMPT": "test task",
                    "AGENT_TASK_URL": "http://example.test",
                    "PYTHONPATH": os.pathsep.join(
                        [str(module_dir), environment.get("PYTHONPATH", "")]
                    ),
                }
            )

            completed = subprocess.run(
                [sys.executable, "-c", SKYVERN_RUNNER],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
            recording = Path(result["recording_url"].removeprefix("file://"))
            screenshots = [
                Path(uri.removeprefix("file://")) for uri in result["screenshot_urls"]
            ]
            self.assertEqual(recording.read_bytes(), b"video")
            self.assertEqual(
                [path.read_bytes() for path in screenshots],
                [b"image-one", b"image-two"],
            )
            self.assertEqual(result["artifact_copy_errors"], [])
            self.assertEqual(recording.parent, output_dir / "skyvern_artifacts" / "recordings")
            self.assertTrue(
                all(
                    path.parent == output_dir / "skyvern_artifacts" / "screenshots"
                    for path in screenshots
                )
            )

    def test_execute_persists_result_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _FakeAdapter(conda_env=None).run_task(
                "http://127.0.0.1:8000/01-minimal.html", "click the button", directory
            )
            self.assertTrue(result.success)
            self.assertEqual(result.stdout.strip(), "ok")
            self.assertTrue((Path(directory) / "stdout.log").is_file())
            self.assertTrue((Path(directory) / "stderr.log").is_file())
            self.assertTrue((Path(directory) / "adapter_result.json").is_file())

    def test_invalid_task_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                _FakeAdapter(conda_env=None).run_task("/relative", "task", directory)

    def test_execute_redacts_secrets_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = _FakeAdapter(conda_env=None)
            result = adapter.execute(
                ["python", "-c", "print('ok')", "secret-token"],
                output_dir=Path(directory),
                redact_values=("secret-token",),
            )
            self.assertNotIn("secret-token", result.command)
            self.assertIn("REDACTED", result.command[-1])

    def test_task_failure_is_not_hidden_by_zero_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            script = (
                "from pathlib import Path; "
                "Path(r'" + str(output / "result.json") + "').write_text("
                "'{\\\"task_success\\\": false, \\\"task_status\\\": \\\"failed\\\"}')"
            )
            adapter = _FakeAdapter(conda_env=None)
            with self.assertRaisesRegex(AgentExecutionError, "task failure") as error:
                adapter.execute(["python", "-c", script], output_dir=output)
            self.assertEqual(error.exception.result.returncode, 0)
            self.assertFalse(error.exception.result.task_success)

    def test_proxy_environment_does_not_bypass_loopback(self) -> None:
        values = _FakeAdapter(conda_env=None).proxy_environment("http://127.0.0.1:8080")
        self.assertEqual(values["HTTP_PROXY"], "http://127.0.0.1:8080")
        self.assertEqual(values["NO_PROXY"], "")
        self.assertEqual(values["no_proxy"], "")

    def test_merged_environment_bypasses_inherited_proxy_for_local_services(self) -> None:
        with patch.dict(
            "os.environ",
            {"HTTP_PROXY": "http://proxy.invalid:3128", "NO_PROXY": "example.org"},
            clear=False,
        ):
            values = _FakeAdapter(conda_env=None).merged_environment()
        self.assertIn("127.0.0.1", values["NO_PROXY"])
        self.assertIn("localhost", values["no_proxy"])
        explicit = _FakeAdapter(conda_env=None).merged_environment(
            _FakeAdapter(conda_env=None).proxy_environment("http://127.0.0.1:8080")
        )
        self.assertEqual(explicit["NO_PROXY"], "")

    @patch.object(BrowserUseAdapter, "execute")
    def test_browseruse_uses_stdin_runner_and_environment(self, execute) -> None:
        execute.return_value = AgentResult("browseruse", ("python", "-"), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            BrowserUseAdapter(conda_env="browser-use").run_task(
                "http://127.0.0.1:8000/01-minimal.html", "click confirm", directory
            )
        command = execute.call_args.args[0]
        kwargs = execute.call_args.kwargs
        self.assertEqual(command[:6], ["conda", "run", "--no-capture-output", "--name", "browser-use", "python"])
        self.assertEqual(command[-1], "-")
        self.assertEqual(kwargs["input_text"].lstrip().splitlines()[0], "import asyncio")
        self.assertEqual(kwargs["env"]["AGENT_TASK_URL"], "http://127.0.0.1:8000/01-minimal.html")
        self.assertEqual(kwargs["env"]["ANONYMIZED_TELEMETRY"], "false")

    @patch.object(BrowserUseAdapter, "execute")
    def test_browseruse_can_target_litellm_base_url(self, execute) -> None:
        execute.return_value = AgentResult("browseruse", ("python", "-"), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            BrowserUseAdapter(
                conda_env="browser-use",
                api_base="http://127.0.0.1:4000/v1",
                api_key="sk-gateway",
                llm_model="claude-sonnet",
            ).run_task("http://127.0.0.1:8000/01-minimal.html", "click confirm", directory)
        kwargs = execute.call_args.kwargs
        self.assertEqual(kwargs["env"]["BROWSER_USE_API_BASE"], "http://127.0.0.1:4000/v1")
        self.assertEqual(kwargs["env"]["BROWSER_USE_API_KEY"], "sk-gateway")
        self.assertEqual(kwargs["redact_values"], ("sk-gateway",))
        self.assertIn("base_url", kwargs["input_text"])
        self.assertIn("is_successful", kwargs["input_text"])

    @patch.object(BrowserUseAdapter, "execute")
    def test_browseruse_passes_upstream_model_for_provider_compatibility(self, execute) -> None:
        execute.return_value = AgentResult("browseruse", ("python", "-"), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            BrowserUseAdapter(
                conda_env="browser-use",
                api_base="http://127.0.0.1:4000/v1",
                api_key="sk-gateway",
                llm_model="chat-gpt",
                upstream_model="deepseek/deepseek-chat",
            ).run_task("http://127.0.0.1:8000/01-minimal.html", "click confirm", directory)
        runner_source = execute.call_args.kwargs["input_text"]
        environment = execute.call_args.kwargs["env"]
        self.assertEqual(environment["BROWSER_USE_UPSTREAM_MODEL"], "deepseek/deepseek-chat")
        self.assertIn("compatibility_model", runner_source)

    @patch.object(BrowserUseAdapter, "execute")
    def test_browseruse_passes_mitm_proxy_to_browser_configuration(self, execute) -> None:
        execute.return_value = AgentResult("browseruse", ("python", "-"), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            adapter = BrowserUseAdapter(conda_env="browser-use")
            adapter.proxy_url = "http://127.0.0.1:8080"
            adapter.run_task("http://127.0.0.1:8000/01-minimal.html", "click", directory)
        environment = execute.call_args.kwargs["env"]
        self.assertEqual(environment["BROWSER_USE_PROXY_SERVER"], "http://127.0.0.1:8080")
        self.assertEqual(environment["BROWSER_USE_NO_PROXY"], "")
        self.assertIn("make_browser", execute.call_args.kwargs["input_text"])

    @patch.object(WebVoyagerAdapter, "execute")
    def test_webvoyager_writes_single_jsonl_task(self, execute) -> None:
        execute.return_value = AgentResult("webvoyager", ("conda",), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            adapter = WebVoyagerAdapter(conda_env="webvoyager", repository="lib/WebVoyager")
            adapter.run_task("http://127.0.0.1:8000/03-flat.html", "fill the form", output)
            task = (output / "webvoyager_task.jsonl").read_text(encoding="utf-8")
            self.assertIn('"web": "http://127.0.0.1:8000/03-flat.html"', task)
            self.assertIn('"ques": "fill the form"', task)
        command = execute.call_args.args[0]
        self.assertIn("--test_file", command)
        self.assertIn("--headless", command)

    def test_webvoyager_detects_only_matching_selenium_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            chrome = home / ".cache/selenium/chrome/linux64/153.0/chrome"
            driver = home / ".cache/selenium/chromedriver/linux64/152.0/chromedriver"
            chrome.parent.mkdir(parents=True)
            driver.parent.mkdir(parents=True)
            chrome.touch()
            driver.touch()
            self.assertFalse(_has_matching_selenium_cache(home))
            matching = home / ".cache/selenium/chromedriver/linux64/153.0/chromedriver"
            matching.parent.mkdir(parents=True)
            matching.touch()
            self.assertTrue(_has_matching_selenium_cache(home))

    @patch.object(WebVoyagerAdapter, "execute")
    def test_webvoyager_sets_openai_proxy_environment(self, execute) -> None:
        execute.return_value = AgentResult("webvoyager", ("conda",), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            WebVoyagerAdapter(
                conda_env="webvoyager",
                repository="lib/WebVoyager",
                api_base="http://127.0.0.1:4000/v1",
                api_key="sk-gateway",
            ).run_task("http://127.0.0.1:8000/03-flat.html", "fill the form", directory)
        environment = execute.call_args.kwargs["env"]
        self.assertEqual(environment["OPENAI_BASE_URL"], "http://127.0.0.1:4000/v1")
        self.assertEqual(environment["OPENAI_API_BASE"], "http://127.0.0.1:4000/v1")
        runner_source = Path("lib/WebVoyager/run.py").read_text(encoding="utf-8")
        self.assertIn("client_kwargs['base_url'] = api_base", runner_source)
        self.assertIn('"task_success": overall_success', runner_source)
        self.assertIn("page_reports_success(driver_task)", runner_source)

    @patch.object(WebVoyagerAdapter, "execute")
    def test_webvoyager_keeps_gateway_key_out_of_argv(self, execute) -> None:
        execute.return_value = AgentResult("webvoyager", (), Path("/tmp/out"), 0, "", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            WebVoyagerAdapter(
                conda_env="webvoyager",
                repository="lib/WebVoyager",
                api_key="secret-gateway-key",
            ).run_task("http://127.0.0.1:8000/01-minimal.html", "click", directory)
        command = execute.call_args.args[0]
        environment = execute.call_args.kwargs["env"]
        self.assertNotIn("secret-gateway-key", command)
        self.assertEqual(environment["WEBVOYAGER_API_KEY"], "secret-gateway-key")

    def test_webvoyager_rejects_extra_args_that_escape_run_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "output_dir"):
            WebVoyagerAdapter(extra_args=("--output_dir", "/tmp/elsewhere"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
