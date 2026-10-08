"""Common process-isolated interface for Web Agent adapters.

The orchestrator imports this module in its own (small) Conda environment.  An
adapter never imports a framework such as ``browser_use`` or Selenium here;
those imports happen in the child process belonging to that framework's Conda
environment.  This keeps incompatible dependency trees and global runtime
state out of the orchestrator process.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence
from urllib.parse import urlparse

from agent_fingerprint.collection.completion import CompletionResult


class AgentAdapterError(RuntimeError):
    """Base exception raised by an adapter."""


@dataclass(slots=True)
class AgentResult:
    """The normalized result of one external Agent invocation."""

    adapter: str
    command: tuple[str, ...]
    output_dir: Path
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    # ``None`` means the framework did not expose a task-level outcome.  A
    # process exit code alone is not enough for agents that can finish their
    # loop without completing the requested browser task.
    task_success: bool | None = None
    task_status: str | None = None
    verification: CompletionResult | None = None

    @property
    def execution_success(self) -> bool:
        """Whether the adapter process itself exited successfully."""

        return self.returncode == 0

    @property
    def success(self) -> bool:
        if self.verification is not None:
            return self.execution_success and self.verification.success
        return self.execution_success and self.task_success is True

    def to_dict(self, *, include_output: bool = True) -> dict[str, object]:
        """Return a JSON-friendly representation for pipeline metadata."""

        data = asdict(self)
        data["success"] = self.success
        data["execution_success"] = self.execution_success
        data["command"] = list(self.command)
        data["output_dir"] = str(self.output_dir)
        if not include_output:
            # stdout/stderr are retained in their dedicated files.  Omitting
            # them from a manifest prevents prompts, URLs, or provider errors
            # from being duplicated into the index most often uploaded to a
            # data store.
            data.pop("stdout", None)
            data.pop("stderr", None)
        return data


class AgentExecutionError(AgentAdapterError):
    """Raised when a child Agent process exits unsuccessfully or times out."""

    def __init__(self, message: str, result: AgentResult):
        super().__init__(message)
        self.result = result


class AgentInterruptedError(AgentExecutionError):
    """Raised when a user interrupt stops the child process.

    Keeping a distinct type lets the pipeline persist its manifest and then
    propagate ``KeyboardInterrupt`` to the CLI, so a matrix does not silently
    continue collecting more tasks after the operator asked it to stop.
    """

    interrupted = True


class BaseAgentAdapter(ABC):
    """Base class exposing ``run_task(url, prompt, output_dir)``.

    Parameters
    ----------
    conda_env:
        Name of the framework-specific Conda environment.  Set ``None`` only
        when ``executable`` is already an absolute path to a Python runtime.
    timeout:
        Optional wall-clock limit in seconds for one task.
    check:
        When true (the default), convert a non-zero child exit code into
        :class:`AgentExecutionError`.  The result is attached to the exception
        so callers can still inspect stdout/stderr and the output directory.
    """

    adapter_name = "base"

    def __init__(
        self,
        *,
        conda_env: str | None = None,
        conda_executable: str = "conda",
        conda_env_path: Path | str | None = None,
        executable: str = "python",
        timeout: float | None = None,
        check: bool = True,
    ) -> None:
        if conda_env_path is None and conda_env:
            # Treat an absolute value passed through the convenient
            # ``conda_env`` argument as a prefix as well.  This keeps direct
            # adapter construction consistent with the CLI's --conda-env
            # handling while retaining named-environment behavior.
            candidate = Path(conda_env).expanduser()
            if candidate.is_absolute():
                conda_env_path = candidate
                conda_env = None
        if conda_env and conda_env_path:
            raise ValueError("Specify either conda_env or conda_env_path, not both")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        self.conda_env = conda_env
        self.conda_executable = conda_executable
        self.conda_env_path = Path(conda_env_path).expanduser() if conda_env_path else None
        self.executable = executable
        self.timeout = timeout
        self.check = check
        # Pipeline sets this when the network probe is an explicit mitmproxy
        # backend.  Keeping the attribute on the common base also makes custom
        # adapters (including slotted subclasses) proxy-aware by default.
        self.proxy_url: str | None = None

    @staticmethod
    def validate_task(url: str, prompt: str) -> None:
        """Reject malformed task inputs before starting a browser process."""

        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute http:// or https:// URL")
        if any(char in url for char in "\r\n"):
            raise ValueError("url must not contain CR/LF characters")
        try:
            # Force validation of malformed ports (urlparse defers this until
            # the .port property is read).
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("url contains an invalid port") from exc
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

    @staticmethod
    def prepare_output_dir(output_dir: Path | str) -> Path:
        path = Path(output_dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def conda_command(self, args: Sequence[str]) -> list[str]:
        """Build a shell-free ``conda run`` command for the child process."""

        if self.conda_env_path is not None:
            target = ["--prefix", str(self.conda_env_path.resolve())]
        elif self.conda_env:
            target = ["--name", self.conda_env]
        else:
            # This is useful for tests and for callers that pass an absolute
            # executable path.  No ``conda run`` means no environment switch.
            return [self.executable, *args]
        return [self.conda_executable, "run", "--no-capture-output", *target, self.executable, *args]

    def merged_environment(self, updates: Mapping[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        explicit_proxy_bypass = bool(updates and ("NO_PROXY" in updates or "no_proxy" in updates))
        if updates:
            env.update({key: str(value) for key, value in updates.items()})
        # Do not let a developer/CI-wide HTTP proxy intercept the local
        # sandbox and gateway.  ``proxy_environment()`` below intentionally
        # overrides this with an empty NO_PROXY when an explicit mitmproxy
        # capture is requested.
        if not explicit_proxy_bypass:
            bypass = [item.strip() for item in (env.get("NO_PROXY") or env.get("no_proxy") or "").split(",") if item.strip()]
            for host in ("localhost", "127.0.0.1", "::1"):
                if host not in bypass:
                    bypass.append(host)
            env["NO_PROXY"] = ",".join(bypass)
            env["no_proxy"] = ",".join(bypass)
        return env

    def proxy_environment(self, proxy_url: str | None) -> dict[str, str]:
        """Return standard proxy variables for a mitmproxy-backed run."""

        if not proxy_url:
            return {}
        # Empty NO_PROXY is intentional: otherwise common inherited values
        # (``localhost,127.0.0.1``) silently bypass mitmproxy and leave the
        # sandbox/gateway requests out of the trace.
        return {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "ALL_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "all_proxy": proxy_url,
            "NO_PROXY": "",
            "no_proxy": "",
        }

    def execute(
        self,
        command: Sequence[str],
        *,
        output_dir: Path,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        redact_values: Sequence[str] = (),
    ) -> AgentResult:
        """Execute one adapter command and persist stdout/stderr beside results."""

        output_dir = Path(output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        command_tuple = tuple(str(item) for item in command)
        # Keep the command used for execution intact, but do not write tokens
        # or API keys into the metadata file that is commonly archived.
        metadata_command = tuple(_redact(item, redact_values) for item in command_tuple)
        process = None
        interrupted = False
        try:
            process = subprocess.Popen(
                list(command_tuple),
                cwd=str(cwd) if cwd else None,
                env=dict(env) if env else None,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=(os.name != "nt"),
            )
            stdout, stderr = process.communicate(input=input_text, timeout=self.timeout)
            returncode = process.returncode
            stdout = stdout or ""
            stderr = stderr or ""
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                _terminate_process_tree(process)
                stdout_after, stderr_after = process.communicate()
            else:
                stdout_after, stderr_after = "", ""
            returncode = -9
            stdout = _coerce_process_output(stdout_after or exc.stdout)
            stderr = _coerce_process_output(stderr_after or exc.stderr) + f"\nTimed out after {self.timeout} seconds."
        except (KeyboardInterrupt, SystemExit) as exc:
            # Ctrl-C/SystemExit can arrive while ``communicate`` is blocked.
            # Always terminate the process group before converting the event
            # into a normal failed result; otherwise Chrome/driver descendants
            # may survive and the orchestrator never gets a manifest.
            if process is not None:
                _terminate_process_tree(process)
                try:
                    stdout_after, stderr_after = process.communicate()
                except (Exception, KeyboardInterrupt, SystemExit):
                    stdout_after, stderr_after = "", ""
            else:
                stdout_after, stderr_after = "", ""
            if isinstance(exc, KeyboardInterrupt):
                returncode = -signal.SIGINT
                reason = "Interrupted by keyboard signal."
                interrupted = True
            else:
                code = getattr(exc, "code", None)
                returncode = int(code) if isinstance(code, int) else -1
                reason = "Child invocation exited via SystemExit."
            stdout = _coerce_process_output(stdout_after)
            stderr = _coerce_process_output(stderr_after) + "\n" + reason
        except (OSError, ValueError) as exc:
            returncode = -1
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}"

        # Apply the same redaction to process output before it is persisted or
        # returned.  Frameworks occasionally echo an API key in an exception
        # or debug line even when the key was supplied through the
        # environment.
        stdout = _redact(stdout, redact_values)
        stderr = _redact(stderr, redact_values)
        result = AgentResult(
            adapter=self.adapter_name,
            command=metadata_command,
            output_dir=output_dir,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            task_success=_read_task_success(output_dir),
            task_status=_read_task_status(output_dir),
        )
        (output_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (output_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        (output_dir / "adapter_result.json").write_text(
            json.dumps(result.to_dict(include_output=False), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if self.check and not result.success:
            if not result.execution_success:
                reason = f"exited with return code {result.returncode}"
            elif result.task_success is False:
                reason = "reported task failure"
            else:
                reason = "did not report a task outcome"
            error_type = AgentInterruptedError if interrupted else AgentExecutionError
            raise error_type(f"{self.adapter_name} {reason}", result)
        return result

    @abstractmethod
    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        """Run one web task and return normalized execution metadata."""


def _coerce_process_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _read_task_success(output_dir: Path) -> bool | None:
    """Read an optional framework-written task outcome without requiring it."""

    for value in _iter_task_metadata(output_dir):
        if isinstance(value.get("task_success"), bool):
            return value["task_success"]
    return None


def _read_task_status(output_dir: Path) -> str | None:
    for value in _iter_task_metadata(output_dir):
        status = value.get("task_status") or value.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip()
    return None


def _iter_task_metadata(output_dir: Path) -> Iterator[dict[str, object]]:
    """Yield valid framework result documents in their precedence order."""

    for filename in ("result.json", "task_result.json", "agent_result.json"):
        path = output_dir / filename
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            yield value


def _redact(value: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "***REDACTED***")
    return value


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate a child process and its process group where supported."""

    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
