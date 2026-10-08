"""Adapter for the open-source ``browser-use`` package.

The package is imported only in a short runner sent to the Browser-use Conda
environment.  Consequently the main pipeline does not need Browser-use (or
its LangChain dependencies) installed in its own environment.
"""

from __future__ import annotations

from pathlib import Path

from .base_adapter import AgentResult, BaseAgentAdapter

_RUNNER = (Path(__file__).resolve().parents[1] / "runners" / "browseruse.py").read_text(encoding="utf-8")


class BrowserUseAdapter(BaseAgentAdapter):
    """Run Browser-use in its dedicated Conda environment."""

    adapter_name = "browseruse"

    def __init__(
        self,
        *,
        conda_env: str | None = "browser-use",
        conda_env_path: Path | str | None = None,
        llm_provider: str = "browser_use",
        llm_model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "RELAY_OPENAI_API_KEY",
        upstream_model: str | None = None,
        max_steps: int | None = None,
        disable_telemetry: bool = True,
        **kwargs,
    ) -> None:
        if max_steps is not None and max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if llm_provider not in {"browser_use", "openai", "anthropic"}:
            raise ValueError("llm_provider must be browser_use, openai, or anthropic")
        if conda_env_path is not None and conda_env == "browser-use":
            conda_env = None
        super().__init__(conda_env=conda_env, conda_env_path=conda_env_path, **kwargs)
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.api_base = api_base.rstrip("/") if api_base else None
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.upstream_model = upstream_model
        self.max_steps = max_steps
        self.disable_telemetry = disable_telemetry
        self.proxy_url: str | None = None

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        output_path = self.prepare_output_dir(output_dir)
        updates = {
            "AGENT_TASK_URL": url,
            "AGENT_TASK_PROMPT": prompt,
            "AGENT_OUTPUT_DIR": str(output_path),
            "BROWSER_USE_LLM_PROVIDER": self.llm_provider,
        }
        key = self.api_key
        if self.api_key:
            # Direct-provider mode (without api_base) reads the conventional
            # provider key from the environment.  Honour an explicitly
            # supplied key without putting it on the command line.
            updates[self.api_key_env] = self.api_key
        if self.llm_model:
            updates["BROWSER_USE_LLM_MODEL"] = self.llm_model
        if self.upstream_model:
            updates["BROWSER_USE_UPSTREAM_MODEL"] = self.upstream_model
        if self.api_base:
            updates["BROWSER_USE_API_BASE"] = self.api_base
            key = self.api_key or self.merged_environment().get(self.api_key_env)
            if key:
                updates["BROWSER_USE_API_KEY"] = key
        if self.max_steps is not None:
            updates["BROWSER_USE_MAX_STEPS"] = str(self.max_steps)
        if self.disable_telemetry:
            # Browser-use documents this environment switch for disabling
            # anonymized telemetry during controlled trace collection.
            updates["ANONYMIZED_TELEMETRY"] = "false"
        if self.proxy_url:
            # Browser-use 0.13 reads these variables into its BrowserProfile,
            # which is the reliable way to pass the proxy to Chromium itself;
            # generic HTTP_PROXY alone may affect Python clients but not the
            # launched browser process.
            updates["BROWSER_USE_PROXY_SERVER"] = self.proxy_url
            updates["BROWSER_USE_NO_PROXY"] = ""
        updates.update(self.proxy_environment(self.proxy_url))
        command = self.conda_command(["-"])
        return self.execute(
            command,
            output_dir=output_path,
            env=self.merged_environment(updates),
            input_text=_RUNNER,
            redact_values=(key,) if key else (),
        )
