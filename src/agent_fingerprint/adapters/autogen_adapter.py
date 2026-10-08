from __future__ import annotations
from pathlib import Path
from .base_adapter import AgentResult, BaseAgentAdapter

_RUNNER = (Path(__file__).resolve().parents[1] / "runners" / "autogen.py").read_text(encoding="utf-8")


class AutoGenAdapter(BaseAgentAdapter):
    """Run an AutoGen agent in its dedicated Conda environment."""

    adapter_name = "autogen"

    def __init__(
        self,
        *,
        conda_env=None,
        conda_env_path=None,
        llm_model="gpt-4o",
        upstream_model=None,
        vision=None,
        api_base=None,
        api_key=None,
        api_key_env="RELAY_OPENAI_API_KEY",
        max_turns=100,
        headless=True,
        save_screenshots=False,
        trace=False,
        **kwargs,
    ):
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        if vision is not None and not isinstance(vision, bool):
            raise ValueError("vision must be true, false, or None")
        if conda_env is None and conda_env_path is None:
            conda_env = "autogen"
        super().__init__(conda_env=conda_env, conda_env_path=conda_env_path, **kwargs)
        self.llm_model = llm_model
        self.upstream_model = upstream_model
        self.vision = vision
        self.api_base = api_base.rstrip("/") if api_base else None
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.max_turns = max_turns
        self.headless = headless
        self.save_screenshots = save_screenshots
        self.trace = trace

    def run_task(self, url, prompt, output_dir):
        self.validate_task(url, prompt)
        output_path = self.prepare_output_dir(output_dir)
        updates = {
            "AGENT_TASK_URL": url,
            "AGENT_TASK_PROMPT": prompt,
            "AGENT_OUTPUT_DIR": str(output_path),
            "AUTOGEN_LLM_MODEL": self.llm_model,
            "AUTOGEN_MAX_TURNS": str(self.max_turns),
            "AUTOGEN_HEADLESS": "0" if not self.headless else "1",
            "AUTOGEN_SCREENSHOTS": "1" if self.save_screenshots else "",
            "AUTOGEN_TRACE": "1" if self.trace else "",
        }
        if self.upstream_model:
            updates["AUTOGEN_UPSTREAM_MODEL"] = self.upstream_model
        if self.vision is not None:
            updates["AUTOGEN_VISION"] = "1" if self.vision else "0"
        if self.api_base:
            updates["AUTOGEN_API_BASE"] = self.api_base
            key = self.api_key or self.merged_environment().get(self.api_key_env)
            if key:
                updates["AUTOGEN_API_KEY"] = key
        elif self.api_key:
            updates[self.api_key_env] = self.api_key
        return self.execute(
            self.conda_command(["-c", _RUNNER]),
            output_dir=output_path,
            env=self.merged_environment(updates),
            redact_values=(self.api_key,) if self.api_key else (),
        )
