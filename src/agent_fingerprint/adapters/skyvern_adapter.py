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


_RUNNER = (Path(__file__).resolve().parents[1] / "runners" / "skyvern.py").read_text(encoding="utf-8")


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
