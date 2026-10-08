"""Manage a local LiteLLM OpenAI-compatible proxy.

The gateway is intentionally controlled by the orchestrator rather than by
each Agent framework.  Framework adapters only receive an OpenAI-compatible
``base_url`` and a model alias, so changing an upstream provider requires no
changes to Browser-use/WebVoyager source code.

The YAML format follows LiteLLM Proxy's ``model_list`` and
``general_settings.master_key`` configuration.  Provider credentials should be
referenced as ``os.environ/VARIABLE_NAME``; this module never writes secret
values into the YAML file.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4000
DEFAULT_MASTER_KEY_ENV = "LITELLM_MASTER_KEY"
BUILTIN_LATENCY_CALLBACK = "gateway.latency_callback.proxy_handler_instance"


class GatewayError(RuntimeError):
    """Raised when the LiteLLM gateway cannot be managed."""


@dataclass(slots=True)
class ModelRoute:
    """One public model alias and its LiteLLM upstream configuration."""

    alias: str
    model: str
    api_key_env: str | None = None
    api_base: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        params = copy.deepcopy(self.params)
        params["model"] = self.model
        if self.api_key_env:
            params["api_key"] = f"os.environ/{self.api_key_env}"
        elif "api_key" in params:
            # A literal key in a config is almost always an accidental secret.
            raise ValueError(f"route {self.alias!r} contains a literal api_key; use api_key_env")
        if self.api_base:
            params["api_base"] = self.api_base
        return {"model_name": self.alias, "litellm_params": params}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelRoute":
        alias = str(raw.get("model_name", "")).strip()
        raw_params = raw.get("litellm_params") or {}
        if not isinstance(raw_params, Mapping):
            raise ValueError(f"route {alias or '<unnamed>'!r} litellm_params must be a mapping")
        params = dict(raw_params)
        model = str(params.pop("model", "")).strip()
        if not alias or not model:
            raise ValueError("each model route requires model_name and litellm_params.model")
        api_key = params.pop("api_key", None)
        api_key_env = None
        if isinstance(api_key, str) and api_key.startswith("os.environ/"):
            api_key_env = api_key.split("/", 1)[1]
            if not api_key_env or not api_key_env.isidentifier():
                raise ValueError(f"route {alias!r} has an invalid api_key environment reference")
        elif api_key is not None:
            raise ValueError(f"route {alias!r} must use os.environ/VAR for api_key")
        api_base = params.pop("api_base", None)
        return cls(alias, model, api_key_env, api_base, params)


class GatewayConfig:
    """Read, validate, and atomically update a LiteLLM YAML config."""

    def __init__(self, path: Path | str, raw: Mapping[str, Any] | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.raw: dict[str, Any] = copy.deepcopy(dict(raw or {}))
        self.raw.setdefault("model_list", [])
        self.raw.setdefault("general_settings", {})
        self.raw.setdefault("litellm_settings", {})
        if not isinstance(self.raw["model_list"], list):
            raise ValueError("LiteLLM model_list must be a list")
        for section in ("general_settings", "litellm_settings"):
            if not isinstance(self.raw[section], Mapping):
                raise ValueError(f"LiteLLM {section} must be a mapping")
        self._routes: dict[str, ModelRoute] = {}
        for item in self.raw["model_list"]:
            if not isinstance(item, Mapping):
                raise ValueError("each LiteLLM model route must be a mapping")
            route = ModelRoute.from_dict(item)
            if route.alias in self._routes:
                raise ValueError(f"duplicate model alias: {route.alias}")
            self._routes[route.alias] = route

    @classmethod
    def load(cls, path: Path | str) -> "GatewayConfig":
        config_path = Path(path).expanduser().resolve()
        if not config_path.is_file():
            raise FileNotFoundError(f"LiteLLM config does not exist: {config_path}")
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
        if not isinstance(raw, Mapping):
            raise ValueError("LiteLLM config root must be a YAML mapping")
        return cls(config_path, raw)

    @property
    def routes(self) -> dict[str, ModelRoute]:
        return dict(self._routes)

    def set_route(
        self,
        alias: str,
        model: str,
        *,
        api_key_env: str | None = None,
        api_base: str | None = None,
        **params: Any,
    ) -> ModelRoute:
        if not isinstance(alias, str) or not isinstance(model, str):
            raise ValueError("alias and model must be strings")
        alias = alias.strip()
        model = model.strip()
        if not alias or not model:
            raise ValueError("alias and model must be non-empty")
        if api_key_env and not api_key_env.isidentifier():
            raise ValueError("api_key_env must be an environment variable name")
        route = ModelRoute(alias, model, api_key_env, api_base, dict(params))
        self._routes[alias] = route
        return route

    def remove_route(self, alias: str) -> None:
        if alias not in self._routes:
            raise KeyError(alias)
        del self._routes[alias]

    def set_master_key(self, key_env: str = DEFAULT_MASTER_KEY_ENV) -> None:
        if not key_env or not key_env.isidentifier():
            raise ValueError("key_env must be an environment variable name")
        self.raw["general_settings"]["master_key"] = f"os.environ/{key_env}"

    def enable_latency_callback(self, callback: str = "gateway.latency_callback.proxy_handler_instance") -> None:
        """Enable the bundled request timing callback in the proxy config."""

        settings = self.raw.setdefault("litellm_settings", {})
        existing = settings.get("callbacks")
        if existing is None:
            # LiteLLM accepts a list across current proxy releases; using a
            # list also leaves room for a caller's other callbacks.
            settings["callbacks"] = [callback]
        elif isinstance(existing, list):
            if callback not in existing:
                existing.append(callback)
        elif existing != callback:
            settings["callbacks"] = [existing, callback]

    def document(self) -> dict[str, Any]:
        document = copy.deepcopy(self.raw)
        document["model_list"] = [route.to_dict() for route in self._routes.values()]
        return document

    def write(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            yaml.safe_dump(self.document(), sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        temporary.replace(self.path)
        return self.path


class LiteLLMGateway:
    """Start, health-check, stop, and reconfigure a local LiteLLM proxy."""

    def __init__(
        self,
        config: GatewayConfig | Path | str,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        executable: str = "litellm",
        master_key_env: str = DEFAULT_MASTER_KEY_ENV,
        startup_timeout: float = 30,
        shutdown_timeout: float = 10,
        output_dir: Path | str | None = None,
    ) -> None:
        self.config = config if isinstance(config, GatewayConfig) else GatewayConfig.load(config)
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if startup_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("gateway timeouts must be positive")
        self.host = host
        self.port = port
        self.executable = str(executable)
        if not master_key_env or not master_key_env.isidentifier():
            raise ValueError("master_key_env must be an environment variable name")
        self.master_key_env = master_key_env
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.output_dir = Path(output_dir).expanduser().resolve() if output_dir else self.config.path.parent
        self.process: subprocess.Popen[str] | None = None
        self._stdout = None
        self._stderr = None
        self._runtime_env: dict[str, str] = os.environ.copy()
        self.started_at: str | None = None
        self._last_returncode: int | None = None
        self._latency_log = self.output_dir / "gateway_events.jsonl"
        self._original_config_path = self.config.path

    def isolate_config(self) -> GatewayConfig:
        """Copy the route document into this run's output directory.

        Dynamic model switching must not rewrite the checked-in shared config;
        this method gives every pipeline run an independent configuration file.
        """

        isolated_path = self.output_dir / "litellm.config.yaml"
        self.config = GatewayConfig(isolated_path, self.config.document())
        return self.config

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health/liveliness"

    @property
    def auth_key(self) -> str | None:
        """Gateway client key exposed to adapters (never persisted by us)."""

        return self._runtime_env.get(self.master_key_env) or os.environ.get(self.master_key_env)

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def missing_environment(self, *, include_master_key: bool = True) -> list[str]:
        """List credential environment variables absent from the current env.

        This is a diagnostic helper, not an unconditional startup gate: local
        providers may intentionally omit an API key, and a caller may inject
        environment values through :meth:`start(env=...)`.
        """

        names: set[str] = set()
        if include_master_key:
            names.add(self.master_key_env)
        for route in self.config.routes.values():
            if route.api_key_env:
                names.add(route.api_key_env)
        return sorted(name for name in names if not os.environ.get(name) and not self._runtime_env.get(name))

    def command(self) -> list[str]:
        return [self.executable, "--config", str(self.config.path), "--host", self.host, "--port", str(self.port)]

    def start(self, *, env: Mapping[str, str] | None = None, write_config: bool = True) -> "LiteLLMGateway":
        if self.process and self.process.poll() is None:
            raise GatewayError("LiteLLM gateway is already running")
        # A caller may construct the manager from a checked-in config while
        # placing runtime logs in a separate directory.  Never rewrite that
        # source file (or materialize a callback over the repository's
        # ``gateway/`` package); make a private per-run copy first.
        config_was_isolated = self.config.path.parent.resolve() != self.output_dir.resolve()
        if config_was_isolated:
            self.isolate_config()
            if not write_config:
                # ``write_config=False`` is used for restarts.  If the caller
                # supplied a source config outside output_dir, still persist
                # the private copy before pointing the child at it.
                self.config.write()
        if write_config:
            self.config.set_master_key(self.master_key_env)
            self.config.enable_latency_callback()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # LiteLLM proxy releases that receive ``config_file_path`` resolve a
        # dotted custom callback relative to the YAML file (for example,
        # ``<run_dir>/gateway/latency_callback.py``), rather than consulting
        # PYTHONPATH.  Materialize the bundled callback beside each isolated
        # config so both old and new proxy releases load the same code.
        self._materialize_builtin_callbacks()
        if write_config:
            self.config.write()
        stdout_path = self.output_dir / "litellm.stdout.log"
        stderr_path = self.output_dir / "litellm.stderr.log"
        # Touch the timing file up front so a run with zero model calls still
        # has a stable, valid (empty JSONL) artifact.
        self._latency_log.touch(exist_ok=True)
        try:
            self._stdout = stdout_path.open("a", encoding="utf-8")
            self._stderr = stderr_path.open("a", encoding="utf-8")
        except OSError as exc:
            self._close_logs()
            raise GatewayError(f"unable to open gateway logs: {exc}") from exc
        process_env = os.environ.copy()
        if env:
            process_env.update({str(key): str(value) for key, value in env.items()})
        # Click (used by the LiteLLM CLI) treats a generic DEBUG environment
        # variable as the value of its --debug option.  CI images and shells
        # sometimes set DEBUG=release (or another non-boolean string), which
        # makes LiteLLM exit before binding its port.  It is not part of this
        # gateway's public configuration, so do not leak it into the child.
        process_env.pop("DEBUG", None)
        _append_loopback_no_proxy(process_env)
        if not process_env.get(self.master_key_env):
            self._close_logs()
            raise GatewayError(
                f"{self.master_key_env} is not set; provide a LiteLLM master key "
                "through the environment or start(env=...)"
            )
        try:
            self._assert_port_available()
        except Exception:
            self._close_logs()
            raise
        from agent_fingerprint.paths import PROJECT_ROOT
        project_root = str(PROJECT_ROOT)
        existing_pythonpath = process_env.get("PYTHONPATH", "")
        process_env["PYTHONPATH"] = project_root + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
        process_env["AGENT_FINGERPRINT_GATEWAY_LOG"] = str(self._latency_log)
        self._runtime_env = process_env
        try:
            self.process = subprocess.Popen(
                self.command(),
                stdout=self._stdout,
                stderr=self._stderr,
                stdin=subprocess.DEVNULL,
                env=process_env,
                start_new_session=(os.name != "nt"),
                text=True,
            )
        except OSError as exc:
            self._close_logs()
            raise GatewayError(f"unable to start LiteLLM: {exc}") from exc
        self.started_at = datetime.now(timezone.utc).isoformat()
        try:
            self.wait_ready()
            self._write_runtime_metadata()
        except BaseException:
            # Include KeyboardInterrupt/SystemExit as well as ordinary
            # startup failures; a direct library caller should never be left
            # with a live proxy because an interrupt arrived during polling.
            try:
                self.stop()
            except BaseException:
                # Preserve the original startup exception.  ``stop`` has its
                # own best-effort process-group cleanup path.
                self.process = None
                self._close_logs()
            raise
        return self

    def _materialize_builtin_callbacks(self) -> None:
        """Copy the bundled callback package next to the active config.

        The copy is intentionally per-run: a LiteLLM child may import the
        module by filename, and a shared source tree would defeat the
        pipeline's output/config isolation.  Only the callback owned by this
        project is handled here; arbitrary user callbacks keep their normal
        LiteLLM loading semantics.
        """

        settings = self.config.raw.get("litellm_settings", {})
        if not isinstance(settings, Mapping):
            return
        callbacks = settings.get("callbacks")
        values = callbacks if isinstance(callbacks, (list, tuple)) else [callbacks]
        if BUILTIN_LATENCY_CALLBACK not in {str(value) for value in values if value is not None}:
            return

        source = Path(__file__).with_name("latency_callback.py").resolve()
        if not source.is_file():
            raise GatewayError(f"bundled latency callback is missing: {source}")
        package_dir = self.config.path.parent / "gateway"
        if package_dir.is_symlink():
            raise GatewayError(f"callback package path is a symlink: {package_dir}")
        package_dir.mkdir(parents=True, exist_ok=True)
        target = package_dir / "latency_callback.py"
        if target.is_symlink():
            raise GatewayError(f"callback module path is a symlink: {target}")
        shutil.copyfile(source, target)
        init_file = package_dir / "__init__.py"
        if init_file.is_symlink():
            raise GatewayError(f"callback package init path is a symlink: {init_file}")
        if not init_file.exists():
            init_file.write_text("# Generated package marker for LiteLLM callback loading.\n", encoding="utf-8")

    def _assert_port_available(self) -> None:
        """Fail before spawning when another process already owns the port.

        A health endpoint on a stale process can otherwise make
        :meth:`wait_ready` return before the newly spawned LiteLLM process has
        failed its bind.  This check is necessarily a small race (the child
        still binds later), but it removes the common and very confusing
        fixed-port collision case; ``wait_ready`` also checks the child PID on
        every poll.
        """

        # ``getaddrinfo`` handles hostnames and IPv4/IPv6 literals.  Try each
        # address family because a wildcard/dual-stack listener can occupy
        # one family while leaving the other apparently free.
        try:
            addresses = socket.getaddrinfo(
                self.host,
                self.port,
                type=socket.SOCK_STREAM,
                flags=socket.AI_PASSIVE,
            )
        except OSError as exc:
            raise GatewayError(f"unable to resolve gateway host {self.host!r}: {exc}") from exc
        seen: set[tuple[int, tuple[Any, ...]]] = set()
        for family, socktype, proto, _canonname, sockaddr in addresses:
            key = (family, tuple(sockaddr))
            if key in seen:
                continue
            seen.add(key)
            sock = socket.socket(family, socktype, proto)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(sockaddr)
            except OSError as exc:
                raise GatewayError(
                    f"gateway address {self.host}:{self.port} is already in use or unavailable: {exc}"
                ) from exc
            finally:
                sock.close()

    def wait_ready(self) -> None:
        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise GatewayError(f"LiteLLM exited during startup with code {self.process.returncode}")
            try:
                request = urllib.request.Request(self.health_url, method="GET")
                key = self.auth_key
                if key:
                    request.add_header("Authorization", f"Bearer {key}")
                # A parent shell may export HTTP(S)_PROXY.  Health checks are
                # always loopback requests and must not be sent to that proxy.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request, timeout=1) as response:
                    if 200 <= response.status < 300:
                        # Do not accept a response from an old listener if the
                        # new child has already exited after a bind/config
                        # error.  The short second check also closes the tiny
                        # race between the HTTP response and process exit.
                        if self.process and self.process.poll() is not None:
                            raise GatewayError(
                                f"LiteLLM exited during startup with code {self.process.returncode}"
                            )
                        time.sleep(0.05)
                        if self.process and self.process.poll() is None:
                            return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            time.sleep(0.2)
        raise GatewayError(f"LiteLLM did not become ready within {self.startup_timeout}s: {last_error}")

    def stop(self) -> None:
        if not self.process:
            return
        process = self.process
        try:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=2)
        finally:
            self._last_returncode = process.returncode
            self._close_logs()
            try:
                self._write_runtime_metadata()
            except Exception:
                # Shutdown must not mask the Agent error that caused cleanup.
                # The stderr/stdout files remain available for diagnosis.
                pass
            self.process = None

    def switch_route(self, alias: str, model: str, *, restart: bool = True, **kwargs: Any) -> None:
        """Change one alias and optionally restart the proxy to load it."""

        self.config.set_route(alias, model, **kwargs)
        self.config.set_master_key(self.master_key_env)
        self.config.write()
        if restart and self.process and self.process.poll() is None:
            self.stop()
            self.start(write_config=False)

    def _close_logs(self) -> None:
        for stream in (self._stdout, self._stderr):
            if stream:
                stream.close()
        self._stdout = None
        self._stderr = None

    def _write_runtime_metadata(self) -> None:
        metadata = {
            "command": self.command(),
            "base_url": self.base_url,
            "health_url": self.health_url,
            "config": str(self.config.path),
            "started_at": self.started_at,
            "running": bool(self.process and self.process.poll() is None),
            "returncode": self.process.returncode if self.process else self._last_returncode,
            "latency_callback": "gateway.latency_callback.proxy_handler_instance",
            "missing_environment": self.missing_environment(include_master_key=True),
        }
        (self.output_dir / "litellm_gateway.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def latency_log_path(self) -> Path:
        """Return the JSONL path used for request timing events.

        LiteLLM's callback API is version-dependent, so the proxy process is
        configured to emit JSON logs when supported and this path is reserved
        for normalized events supplied by a callback or test proxy.  The
        PipelineRunner also records client-side request boundaries here.
        """

        return self._latency_log

    def record_event(self, event: Mapping[str, Any]) -> None:
        """Append a sanitized, structured gateway timing event."""

        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe = {
            str(k): _sanitize_event_value(v)
            for k, v in event.items()
            if str(k).lower() not in {"api_key", "authorization", "prompt", "messages", "content"}
        }
        safe.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        with self._latency_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False, default=str) + "\n")

    def __enter__(self) -> "LiteLLMGateway":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()


def _sanitize_event_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        forbidden = {"api_key", "authorization", "prompt", "messages", "content", "input", "output"}
        return {
            str(key): _sanitize_event_value(item)
            for key, item in value.items()
            if str(key).lower() not in forbidden
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_event_value(item) for item in value]
    return value


def _append_loopback_no_proxy(environment: dict[str, str]) -> None:
    """Keep local upstreams off an inherited corporate HTTP proxy."""

    values = [
        item.strip()
        for item in (environment.get("NO_PROXY") or environment.get("no_proxy") or "").split(",")
        if item.strip()
    ]
    for host in ("localhost", "127.0.0.1", "::1"):
        if host not in values:
            values.append(host)
    joined = ",".join(values)
    environment["NO_PROXY"] = joined
    environment["no_proxy"] = joined


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Agent-Fingerprint LiteLLM gateway")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--executable", default="litellm")
    parser.add_argument("--master-key-env", default=DEFAULT_MASTER_KEY_ENV)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    gateway = LiteLLMGateway(
        args.config,
        host=args.host,
        port=args.port,
        executable=args.executable,
        master_key_env=args.master_key_env,
        output_dir=args.output_dir,
    )
    try:
        gateway.start()
        print(f"LiteLLM ready at {gateway.base_url}", flush=True)
        while gateway.process and gateway.process.poll() is None:
            time.sleep(0.5)
        return gateway.process.returncode if gateway.process else 0
    except KeyboardInterrupt:
        return 130
    except GatewayError as exc:
        print(f"litellm_gateway: {exc}", flush=True)
        return 2
    finally:
        gateway.stop()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
