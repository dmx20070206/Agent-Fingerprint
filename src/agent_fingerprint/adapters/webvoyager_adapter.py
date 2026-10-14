"""Subprocess adapter for the checked-out WebVoyager research repository."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Sequence

from .base_adapter import AgentResult, BaseAgentAdapter

from agent_fingerprint.paths import PROJECT_ROOT
DEFAULT_REPOSITORY = PROJECT_ROOT / "third_party" / "WebVoyager"


def _matching_selenium_pair(home: Path | None = None) -> tuple[Path, Path] | None:
    """Choose one exact cached version, including its platform, deterministically."""

    cache_root = (home or Path.home()) / ".cache" / "selenium"
    browsers = {
        (executable.parent.parent.name, executable.parent.name): executable
        for executable in cache_root.glob("chrome/*/*/chrome")
        if executable.is_file()
    }
    drivers = {
        (executable.parent.parent.name, executable.parent.name): executable
        for executable in cache_root.glob("chromedriver/*/*/chromedriver")
        if executable.is_file()
    }
    matches = browsers.keys() & drivers.keys()
    native_platform = {
        "linux": "linux64",
        "darwin": "mac-arm64" if platform.machine() in {"arm64", "aarch64"} else "mac-x64",
        "win32": "win64" if sys.maxsize > 2**32 else "win32",
    }.get(sys.platform)
    matches = {key for key in matches if key[0] == native_platform}
    if not matches:
        return None
    key = max(matches, key=lambda key: (tuple(int(n) for n in key[1].split('.')), key[0]))
    return browsers[key], drivers[key]


def _has_matching_selenium_cache(home: Path | None = None) -> bool:
    return _matching_selenium_pair(home) is not None


def _configure_cached_browser(environment: dict[str, str]) -> None:
    # Never combine an explicit binary with an unrelated automatic selection.
    if any(environment.get(key) for key in (
        "WEBVOYAGER_CHROME_BINARY", "WEBVOYAGER_CHROMEDRIVER",
        "CHROME_BINARY", "CHROMEDRIVER",
    )):
        return
    pair = _matching_selenium_pair()
    if pair:
        environment["WEBVOYAGER_CHROME_BINARY"] = str(pair[0])
        environment["WEBVOYAGER_CHROMEDRIVER"] = str(pair[1])


class WebVoyagerAdapter(BaseAgentAdapter):
    """Run ``third_party/WebVoyager/run.py`` in a dedicated Conda environment.

    WebVoyager consumes JSONL tasks rather than a single URL/prompt pair.  The
    adapter creates a one-line task file in ``output_dir`` and passes it to the
    repository's existing CLI, preserving the upstream implementation and its
    screenshots/logging behavior.
    """

    adapter_name = "webvoyager"

    def __init__(
        self,
        *,
        conda_env: str | None = "webvoyager",
        conda_env_path: Path | str | None = None,
        repository: Path | str = DEFAULT_REPOSITORY,
        max_iter: int = 100,
        headless: bool = True,
        text_only: bool = False,
        save_accessibility_tree: bool = False,
        api_model: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "RELAY_OPENAI_API_KEY",
        api_base: str | None = None,
        max_attached_imgs: int | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        pass_api_key_arg: bool = False,
        extra_args: Sequence[str] = (),
        **kwargs,
    ) -> None:
        if max_iter <= 0:
            raise ValueError("max_iter must be positive")
        if max_attached_imgs is not None and max_attached_imgs <= 0:
            raise ValueError("max_attached_imgs must be positive")
        if not 0 <= (temperature if temperature is not None else 0) <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if conda_env_path is not None and conda_env == "webvoyager":
            # Supplying only an absolute path should select Conda --prefix;
            # the default name must not make the base class see two targets.
            conda_env = None
        # A source checkout is also useful inside the project's own
        # ``agent-fingerprint`` environment.  Conda is intentionally not a
        # runtime dependency of the orchestrator, so when it is unavailable
        # fall back to the current interpreter rather than failing before the
        # WebVoyager process starts.  If Conda is present, the named
        # environment remains the default isolation boundary.
        if conda_env == "webvoyager" and shutil.which("conda") is None:
            conda_env = None
        super().__init__(conda_env=conda_env, conda_env_path=conda_env_path, **kwargs)
        self.repository = Path(repository).expanduser().resolve()
        self.max_iter = max_iter
        self.headless = headless
        self.text_only = text_only
        self.save_accessibility_tree = save_accessibility_tree
        self.api_model = api_model
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.api_base = api_base.rstrip("/") if api_base else None
        self.max_attached_imgs = max_attached_imgs
        self.temperature = temperature
        self.seed = seed
        self.pass_api_key_arg = pass_api_key_arg
        self.extra_args = tuple(str(arg) for arg in extra_args)
        protected = {
            "--test_file",
            "--output_dir",
            "--download_dir",
            "--api_key",
            "--api_model",
            "--proxy_server",
        }
        for argument in self.extra_args:
            option = argument.split("=", 1)[0]
            if option in protected:
                raise ValueError(f"extra_args cannot override isolated WebVoyager option {option}")
        self.proxy_url: str | None = None

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        if not self.repository.is_dir():
            raise FileNotFoundError(f"WebVoyager repository does not exist: {self.repository}")
        run_script = self.repository / "run.py"
        if not run_script.is_file():
            raise FileNotFoundError(f"WebVoyager entrypoint does not exist: {run_script}")

        output_path = self.prepare_output_dir(output_dir)
        task_file = output_path / "webvoyager_task.jsonl"
        task_file.write_text(
            json.dumps({"id": "single", "web": url, "ques": prompt}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        download_dir = output_path / "downloads"
        download_dir.mkdir(exist_ok=True)

        args = [
            str(run_script),
            "--test_file",
            str(task_file),
            "--output_dir",
            str(output_path),
            "--download_dir",
            str(download_dir),
            "--max_iter",
            str(self.max_iter),
        ]
        if self.headless:
            args.append("--headless")
        if self.text_only:
            args.append("--text_only")
        if self.save_accessibility_tree:
            args.append("--save_accessibility_tree")
        if self.api_model:
            args.extend(["--api_model", self.api_model])
        key = self.api_key or os.environ.get(self.api_key_env)
        if key:
            # The vendored runner supports an environment fallback.  Keeping
            # the secret out of argv prevents it from appearing in ``ps`` or
            # process metadata.  ``pass_api_key_arg`` remains available for
            # older external WebVoyager checkouts that lack that fallback.
            environment_key = key
        else:
            environment_key = None
        if self.max_attached_imgs is not None:
            args.extend(["--max_attached_imgs", str(self.max_attached_imgs)])
        if self.temperature is not None:
            args.extend(["--temperature", str(self.temperature)])
        if self.seed is not None:
            args.extend(["--seed", str(self.seed)])
        args.extend(self.extra_args)

        environment = self.merged_environment()
        if environment_key:
            environment["WEBVOYAGER_API_KEY"] = environment_key
            environment.setdefault("RELAY_OPENAI_API_KEY", environment_key)
        # OpenAI's SDK (used by upstream WebVoyager) honors OPENAI_BASE_URL,
        # allowing the proxy to be introduced without modifying run.py.
        if self.api_base:
            environment["OPENAI_BASE_URL"] = self.api_base
            environment["OPENAI_API_BASE"] = self.api_base
        # Offline discovery alone can still mix the newest Chrome with an old
        # driver. Pin both paths in this child only; leave the shared cache and
        # other agents' environments untouched.
        _configure_cached_browser(environment)
        environment.update(self.proxy_environment(self.proxy_url))
        if self.proxy_url:
            args.extend(["--proxy_server", self.proxy_url])
        if self.pass_api_key_arg and environment_key:
            args.extend(["--api_key", environment_key])
        if (self.api_model or "").rsplit("/", 1)[-1].lower().startswith("qwen3.5-"):
            # Install compatibility fixes only inside this framework/model's
            # child process; keep upstream and other model launchers intact.
            args.insert(0, str(Path(__file__).resolve().parents[1] / "runners" / "webvoyager.py"))

        return self.execute(
            self.conda_command(args),
            output_dir=output_path,
            cwd=self.repository,
            env=environment,
            redact_values=(environment_key,) if environment_key else (),
        )
