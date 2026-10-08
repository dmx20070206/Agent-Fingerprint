"""A deterministic child-process Agent used for local pipeline smoke tests.

It is intentionally not an intelligent Web Agent.  The child performs an HTTP
GET, emits one synthetic UI event, and (when configured) calls the local
OpenAI-compatible gateway.  Keeping it as a normal adapter makes the complete
CLI lifecycle testable on machines without Conda, Chrome, or model keys.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .base_adapter import AgentResult, BaseAgentAdapter


_RUNNER = (Path(__file__).resolve().parents[1] / "runners" / "mock.py").read_text(encoding="utf-8")


class MockAgentAdapter(BaseAgentAdapter):
    """Run the deterministic smoke-test child in the current Python env."""

    adapter_name = "mock-agent"

    def __init__(
        self,
        *,
        gateway_url: str | None = None,
        gateway_key: str | None = None,
        model: str | None = None,
        run_id: str | None = None,
        executable: str = sys.executable,
        **kwargs,
    ) -> None:
        super().__init__(conda_env=None, executable=executable, **kwargs)
        self.gateway_url = gateway_url.rstrip("/") if gateway_url else None
        self.gateway_key = gateway_key
        self.model = model
        self.run_id = run_id
        self.proxy_url: str | None = None

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        path = self.prepare_output_dir(output_dir)
        updates = {
            "AGENT_TASK_URL": url,
            "AGENT_TASK_PROMPT": prompt,
            "AGENT_OUTPUT_DIR": str(path),
            "AGENT_RUN_ID": self.run_id or path.parent.name,
        }
        if self.gateway_url:
            updates["AGENT_GATEWAY_URL"] = self.gateway_url
            updates["AGENT_GATEWAY_KEY"] = self.gateway_key or "mock-key"
        if self.model:
            updates["AGENT_MODEL"] = self.model
        if self.proxy_url:
            updates["AGENT_PROXY_URL"] = self.proxy_url
        updates.update(self.proxy_environment(self.proxy_url))
        return self.execute(
            self.conda_command(["-c", _RUNNER]),
            output_dir=path,
            env=self.merged_environment(updates),
            redact_values=(self.gateway_key,) if self.gateway_key else (),
        )
