"""Process-isolated adapter for local or remotely hosted Skyvern.

Skyvern is intentionally imported only in the child process.  With a LiteLLM
API base configured, the child starts Skyvern's embedded engine and routes all
model calls through that OpenAI-compatible endpoint.  Without one, it retains
the SDK's Skyvern Cloud/self-hosted API mode for backwards compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .base_adapter import AgentResult, BaseAgentAdapter


_RUNNER = r'''
import asyncio, json, os, shutil, traceback
from pathlib import Path
from urllib.parse import unquote, urlsplit

async def main():
    from skyvern import Skyvern
    output_dir = Path(os.environ["AGENT_OUTPUT_DIR"])
    llm_api_base = os.environ.get("SKYVERN_LLM_API_BASE")
    if llm_api_base:
        from skyvern.schemas.llm import LLMConfig

        model = os.environ.get("SKYVERN_LLM_MODEL", "chat-gpt")
        # The provider prefix makes Skyvern/LiteLLM use the OpenAI-compatible
        # client.  LiteLLM strips it before sending the public alias to the
        # experiment gateway.
        llm_model = model if "/" in model else "openai/" + model
        protocol = os.environ.get("SKYVERN_LLM_PROTOCOL", "native").strip().lower()
        protocol_params = {}
        if protocol == "json":
            # Skyvern v2 prompts are JSON action protocol prompts, not OpenAI
            # function/tool prompts.  JSON object mode prevents an
            # OpenAI-compatible Gemini route from returning an empty assistant
            # message while leaving the other agents untouched.
            protocol_params["response_format"] = {"type": "json_object"}
        elif protocol not in {"text", "native"}:
            raise ValueError(
                "SKYVERN_LLM_PROTOCOL must be one of: json, text, native"
            )
        llm_config = LLMConfig(
            model_name=llm_model,
            required_env_vars=[],
            supports_vision=True,
            add_assistant_prefix=False,
            litellm_params={
                "api_key": os.environ.get("SKYVERN_LLM_API_KEY") or "sk-placeholder",
                "api_base": llm_api_base,
                "model_info": {"model_name": llm_model},
                **protocol_params,
            },
        )
        client = Skyvern.local(use_in_memory_db=True, llm_config=llm_config)
    else:
        kwargs = {"api_key": os.environ.get("SKYVERN_API_KEY", "")}
        if os.environ.get("SKYVERN_BASE_URL"):
            kwargs["base_url"] = os.environ["SKYVERN_BASE_URL"]
        client = Skyvern(**kwargs)
    call = {"prompt": os.environ["AGENT_TASK_PROMPT"], "url": os.environ["AGENT_TASK_URL"],
            "wait_for_completion": True}
    if os.environ.get("SKYVERN_MAX_STEPS"):
        call["max_steps"] = int(os.environ["SKYVERN_MAX_STEPS"])
    if os.environ.get("SKYVERN_TIMEOUT"):
        call["timeout"] = float(os.environ["SKYVERN_TIMEOUT"])
    try:
        result = await client.run_task(**call)
        def value(name, default=None):
            item = getattr(result, name, default)
            return item.value if hasattr(item, "value") else item

        # Embedded Skyvern stores artifacts below a temporary directory which
        # client.aclose() removes.  Copy every local artifact exposed by the
        # task result into the pipeline-owned run directory before closing the
        # client so recordings and screenshots survive the child process.
        artifact_copy_errors = []
        artifact_root = output_dir / "skyvern_artifacts"

        def persist_local_artifact(uri, category):
            if not isinstance(uri, str) or not uri.startswith("file://"):
                return uri
            parsed = urlsplit(uri)
            if parsed.netloc not in {"", "localhost"}:
                artifact_copy_errors.append(f"unsupported file URI host: {parsed.netloc}")
                return uri
            source = Path(unquote(parsed.path))
            if not source.is_file():
                artifact_copy_errors.append(f"artifact file does not exist: {source}")
                return uri
            destination_dir = artifact_root / category
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / source.name
            try:
                shutil.copy2(source, destination)
            except OSError as exc:
                artifact_copy_errors.append(f"failed to copy {source}: {exc}")
                return uri
            return destination.resolve().as_uri()

        recording_url = persist_local_artifact(value("recording_url"), "recordings")
        raw_screenshot_urls = value("screenshot_urls") or []
        if isinstance(raw_screenshot_urls, str):
            raw_screenshot_urls = [raw_screenshot_urls]
        screenshot_urls = [
            persist_local_artifact(uri, "screenshots") for uri in raw_screenshot_urls
        ]
        status = value("status")
        status_text = str(status).lower() if status is not None else ""
        success = status_text in {"completed", "succeeded", "success", "passed"}
        if not status_text:
            success = None
        payload = {"url": call["url"], "prompt": call["prompt"], "run_id": value("run_id"),
                   "status": status, "task_status": status, "task_success": success,
                   "output": value("output"), "failure_reason": value("failure_reason"),
                   "recording_url": recording_url, "screenshot_urls": screenshot_urls,
                   "artifact_copy_errors": artifact_copy_errors,
                   "step_count": value("step_count")}
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        (output_dir / "result.json").write_text(encoded, encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, default=str))
        if success is False: raise SystemExit(3)
    finally:
        await client.aclose()

if __name__ == "__main__":
    try: asyncio.run(main())
    except Exception:
        traceback.print_exc()
        raise
'''


class SkyvernAdapter(BaseAgentAdapter):
    """Run Skyvern in embedded mode through LiteLLM, or call a remote API."""

    adapter_name = "skyvern"

    def __init__(
        self,
        *,
        conda_env: str | None = "skyvern",
        conda_env_path: Path | str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
        max_steps: int | None = None,
        timeout: float | None = None,
        **kwargs,
    ) -> None:
        if max_steps is not None and max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if conda_env_path is not None and conda_env == "skyvern":
            conda_env = None
        super().__init__(
            conda_env=conda_env,
            conda_env_path=conda_env_path,
            timeout=timeout,
            **kwargs,
        )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") if base_url else None
        self.llm_model = llm_model
        self.llm_api_base = llm_api_base.rstrip("/") if llm_api_base else None
        self.llm_api_key = llm_api_key
        self.max_steps = max_steps
        self.proxy_url: str | None = None

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        path = self.prepare_output_dir(output_dir)
        updates = {"AGENT_TASK_URL": url, "AGENT_TASK_PROMPT": prompt,
                   "AGENT_OUTPUT_DIR": str(path), "ANONYMIZED_TELEMETRY": "false"}
        # Skyvern applies its public-service SSRF policy even in the embedded
        # local engine.  The pipeline deliberately serves the task fixture on
        # a loopback address, so allow exactly that already-validated task
        # host instead of disabling URL validation or allowing private ranges.
        if self.llm_api_base:
            updates["ALLOWED_HOSTS"] = json.dumps([urlparse(url).hostname])
            # Pipeline runs are unattended and may execute on nodes without an
            # X server. Skyvern otherwise defaults to chromium-headful.
            updates["BROWSER_TYPE"] = "chromium-headless"
        if self.api_key: updates["SKYVERN_API_KEY"] = self.api_key
        if self.base_url: updates["SKYVERN_BASE_URL"] = self.base_url
        if self.llm_model: updates["SKYVERN_LLM_MODEL"] = self.llm_model
        if self.llm_api_base: updates["SKYVERN_LLM_API_BASE"] = self.llm_api_base
        if self.llm_api_key: updates["SKYVERN_LLM_API_KEY"] = self.llm_api_key
        if self.max_steps is not None: updates["SKYVERN_MAX_STEPS"] = str(self.max_steps)
        if self.timeout is not None: updates["SKYVERN_TIMEOUT"] = str(self.timeout)
        updates.update(self.proxy_environment(self.proxy_url))
        secrets = tuple(value for value in (self.api_key, self.llm_api_key) if value)
        return self.execute(self.conda_command(["-c", _RUNNER]), output_dir=path,
                            env=self.merged_environment(updates),
                            redact_values=secrets)
