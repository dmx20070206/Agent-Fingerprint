"""Optional adapter for an already-running Agent-E HTTP service.

Agent-E's checked-out research repository is primarily an interactive UI and
does not expose a stable one-shot CLI like WebVoyager.  Rather than pretending
that ``python -m ae.main`` is batch-safe, this adapter targets its documented
``POST /execute_task`` service.  The service itself should be launched in the
dedicated Agent-E Conda environment; the request and response handling still
run in a short-lived child process.
"""

from __future__ import annotations

from pathlib import Path

from .base_adapter import AgentResult, BaseAgentAdapter, AgentAdapterError


_RUNNER = (Path(__file__).resolve().parents[1] / "runners" / "agente.py").read_text(encoding="utf-8")


class AgentEAdapter(BaseAgentAdapter):
    """Submit one command to an Agent-E ``/execute_task`` endpoint."""

    adapter_name = "agente"

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        conda_env: str | None = "agent-e",
        conda_env_path: Path | str | None = None,
        http_timeout: float | None = None,
        max_steps: int | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
        **kwargs,
    ) -> None:
        if http_timeout is not None and http_timeout <= 0:
            raise ValueError("http_timeout must be positive")
        if max_steps is not None and max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if conda_env_path is not None and conda_env == "agent-e":
            conda_env = None
        super().__init__(conda_env=conda_env, conda_env_path=conda_env_path, **kwargs)
        self.endpoint_url = endpoint_url.rstrip("/") if endpoint_url else None
        self.http_timeout = http_timeout
        self.max_steps = max_steps
        self.llm_model = llm_model
        self.llm_api_base = llm_api_base.rstrip("/") if llm_api_base else None
        self.llm_api_key = llm_api_key
        self.proxy_url: str | None = None

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        if not self.endpoint_url:
            raise AgentAdapterError(
                "Agent-E has no stable batch CLI; start ae.server.api_routes in its "
                "Conda environment and pass endpoint_url='http://127.0.0.1:8080/execute_task'"
            )
        output_path = self.prepare_output_dir(output_dir)
        command_text = f"Open {url} and complete this task: {prompt}"
        environment = self.merged_environment(
            {
                "AGENTE_ENDPOINT": self.endpoint_url,
                "AGENTE_COMMAND": command_text,
                "AGENTE_RUN_ID": output_path.name,
                "AGENTE_HTTP_TIMEOUT": str(self.http_timeout or self.timeout or 1800),
                "AGENT_OUTPUT_DIR": str(output_path),
            }
        )
        if self.max_steps is not None:
            environment["AGENTE_MAX_STEPS"] = str(self.max_steps)
        if self.llm_model:
            environment["AGENTE_LLM_MODEL"] = self.llm_model
        if self.llm_api_base:
            environment["AGENTE_LLM_API_BASE"] = self.llm_api_base
        if self.llm_api_key:
            environment["AGENTE_LLM_API_KEY"] = self.llm_api_key
        environment.update(self.proxy_environment(self.proxy_url))
        return self.execute(
            self.conda_command(["-"]),
            output_dir=output_path,
            env=environment,
            input_text=_RUNNER,
            redact_values=(self.llm_api_key,) if self.llm_api_key else (),
        )
