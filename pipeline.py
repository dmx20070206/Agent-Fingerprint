"""End-to-end data-collection cycle for the Agent-Fingerprint project.

``PipelineRunner`` owns the lifecycle described in the project design:

    sandbox HTTP server -> LiteLLM gateway -> network probe -> Agent adapter
    -> artifact collection -> reverse-order cleanup

The default tests use fake adapters/tools, so this orchestration layer can be
verified without a paid LLM API or browser installation.  Real adapters can be
passed through ``adapter_factory`` in exactly the same way.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from adapters import (
    AgentEAdapter,
    AutoGenAdapter,
    BrowserUseAdapter,
    ManualAdapter,
    MockAgentAdapter,
    SkyvernAdapter,
    WebVoyagerAdapter,
)
from adapters.base_adapter import AgentInterruptedError, AgentResult
from completion import CompletionResult, VERIFIED_AGENTS
from gateway import GatewayConfig, LiteLLMGateway
from probes.traffic_sniffer import CaptureResult, CaptureSession, TrafficSniffer
from sandbox.server import TraceStore, create_server

__all__ = ["CycleResult", "PipelineRunner"]


FINGERPRINT_FILES = {
    "l1": "l1_http_tls.json",
    "l2": "l2_browser_static.json",
    "l3": "l3_browser_dynamic.json",
    "l4": "l4_agent_trace.json",
}
FINGERPRINT_DIR = "fingerprints"
ARTIFACT_DIR = "artifacts"
RUN_INDEX_FILE = "index.jsonl"


@dataclass(slots=True)
class CycleResult:
    """A normalized result and manifest for one URL/Agent combination."""

    run_id: str
    output_dir: Path
    manifest: dict[str, Any]
    agent_result: AgentResult | None = None

    @property
    def success(self) -> bool:
        return bool(self.manifest.get("status") == "success")


@dataclass(slots=True)
class _ServerHandle:
    server: Any
    thread: threading.Thread
    trace_store: TraceStore
    _stopped: bool = False

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._stopped:
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._stopped = True


class PipelineRunner:
    """Run one or many isolated collection cycles."""

    def __init__(
        self,
        *,
        output_root: Path | str = "data/runs",
        sandbox_directory: Path | str | None = None,
        start_sandbox: bool = True,
        inject_monitor: bool = True,
        gateway: LiteLLMGateway | None = None,
        gateway_config: GatewayConfig | Path | str | None = None,
        gateway_factory: Callable[..., Any] | None = None,
        gateway_configurator: Callable[[GatewayConfig], None] | None = None,
        gateway_options: Mapping[str, Any] | None = None,
        sniffer_factory: Callable[..., Any] | None = None,
        adapter_factory: Mapping[str, Callable[..., Any]] | None = None,
        adapter_options: Mapping[str, Mapping[str, Any]] | None = None,
        use_network_probe: bool = True,
        collect_l1: bool = True,
        collect_l2: bool = True,
        collect_l3: bool = True,
        collect_l4: bool = True,
        interaction_delay_seconds: float = 0.0,
        trace_grace_seconds: float = 0.15,
        trace_finalize_timeout_seconds: float = 8.0,
        # Relay-backed LLM calls can legitimately take several minutes while
        # an upstream provider queues or retries a request.  Keep the parent
        # wall-clock limit comfortably above the per-request limits used by
        # the framework adapters.
        agent_timeout: float | None = 1800.0,
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.sandbox_directory = sandbox_directory
        self.start_sandbox = start_sandbox
        self.inject_monitor = inject_monitor
        self.gateway = gateway
        self.gateway_config = gateway_config
        self.gateway_factory = gateway_factory
        self.gateway_configurator = gateway_configurator
        self.gateway_options = dict(gateway_options or {})
        self.sniffer_factory = sniffer_factory
        self.adapter_factory = dict(adapter_factory or {})
        self.adapter_options = {key: dict(value) for key, value in (adapter_options or {}).items()}
        self.use_network_probe = use_network_probe
        self.collect_l1 = bool(collect_l1)
        self.collect_l2 = bool(collect_l2)
        self.collect_l3 = bool(collect_l3)
        self.collect_l4 = bool(collect_l4)
        if interaction_delay_seconds < 0:
            raise ValueError("interaction_delay_seconds cannot be negative")
        self.interaction_delay_seconds = float(interaction_delay_seconds)
        if trace_grace_seconds < 0:
            raise ValueError("trace_grace_seconds cannot be negative")
        self.trace_grace_seconds = trace_grace_seconds
        if trace_finalize_timeout_seconds <= 0:
            raise ValueError("trace_finalize_timeout_seconds must be positive")
        self.trace_finalize_timeout_seconds = float(trace_finalize_timeout_seconds)
        if agent_timeout is not None and agent_timeout <= 0:
            raise ValueError("agent_timeout must be positive or None")
        self.agent_timeout = agent_timeout
        self._sandbox: _ServerHandle | None = None
        self._sandbox_owned = False

    def start_sandbox_server(self, run_id: str = "default", *, owned: bool = False) -> _ServerHandle:
        _validate_run_id(run_id)
        if self._sandbox is not None:
            return self._sandbox
        trace_store = TraceStore()
        server = create_server(
            port=0,
            directory=self.sandbox_directory or Path(__file__).resolve().parent / "sandbox" / "static",
            trace_store=trace_store,
            inject_monitor=self.inject_monitor and (self.collect_l2 or self.collect_l3),
            run_id=run_id,
            interaction_delay_seconds=self.interaction_delay_seconds,
        )
        thread = threading.Thread(target=server.serve_forever, name="sandbox-http", daemon=True)
        thread.start()
        self._sandbox = _ServerHandle(server, thread, trace_store)
        self._sandbox_owned = owned
        return self._sandbox

    def _ensure_sandbox_server(self, run_id: str) -> tuple[_ServerHandle, bool]:
        if self._sandbox is not None:
            return self._sandbox, False
        return self.start_sandbox_server(run_id, owned=True), True

    def stop_sandbox_server(self) -> None:
        if self._sandbox:
            self._sandbox.stop()
            self._sandbox = None
            self._sandbox_owned = False

    def _new_run_dir(
        self, run_id: str | None, *, agent_name: str = "agent", model: str | None = None
    ) -> tuple[str, Path]:
        if run_id is None:
            agent_part = _short_name(agent_name)
            model_part = _short_model(model or "default")
            timestamp = datetime.now().strftime("%m%d_%H%M")
            identifier = f"{agent_part}_{model_part}_{timestamp}"
        else:
            identifier = run_id
        _validate_run_id(identifier)
        date_dir = self.output_root / datetime.now().strftime("%Y-%m-%d")
        path = date_dir / identifier
        if path.exists():
            index = 2
            while (date_dir / f"{identifier}_{index}").exists():
                index += 1
            identifier = f"{identifier}_{index}"
            _validate_run_id(identifier)
            path = date_dir / identifier
        path.mkdir(parents=True, exist_ok=False)
        return identifier, path

    def _make_gateway(self, run_dir: Path) -> LiteLLMGateway | None:
        if self.gateway_factory:
            return _call_factory(self.gateway_factory, run_dir=run_dir)
        if self.gateway:
            return self.gateway
        if self.gateway_config is None:
            return None
        config = (
            self.gateway_config
            if isinstance(self.gateway_config, GatewayConfig)
            else GatewayConfig.load(self.gateway_config)
        )
        # Never mutate the source config in config/.  Each cycle gets a private
        # copy so model switching and concurrent runs are isolated.
        isolated = GatewayConfig(run_dir / "logs" / "litellm.config.yaml", config.document())
        if self.gateway_configurator:
            self.gateway_configurator(isolated)
        options = dict(self.gateway_options)
        options.setdefault("output_dir", run_dir / "logs")
        gateway = LiteLLMGateway(isolated, **options)
        return gateway

    @staticmethod
    def _default_route_alias(agent_name: str, model: str | None) -> str:
        if model:
            return model
        # Both supported adapters speak the OpenAI-compatible API when a
        # gateway is present.  Keeping one stable alias makes matrix results
        # comparable across frameworks.
        return "chat-gpt"

    def _configure_gateway_route(
        self,
        gateway: Any,
        *,
        agent_name: str,
        model: str | None,
        upstream_model: str | None,
        provider_key_env: str | None,
        route_alias: str | None,
    ) -> dict[str, Any] | None:
        """Apply a per-run route switch before the proxy is started.

        The source YAML is copied by :meth:`_make_gateway`; this method only
        mutates that private copy.  A custom gateway may expose the same
        ``config``/``switch_route`` methods, or simply ignore route metadata.
        """

        if not upstream_model:
            return None
        alias = route_alias or self._default_route_alias(agent_name, model)
        # A provider switch must also switch credentials.  If callers omit
        # --provider-key-env, infer the conventional key from the provider
        # prefix instead of silently retaining the source route's key (for
        # example RELAY_OPENAI_API_KEY when routing chat-gpt to DeepSeek).
        effective_key_env = provider_key_env or _infer_provider_key_env(upstream_model)
        config = getattr(gateway, "config", None)
        if config is not None and hasattr(config, "set_route") and not _is_running(gateway):
            existing = getattr(config, "routes", {}).get(alias)
            params = dict(getattr(existing, "params", {}) or {}) if existing else {}
            api_base = getattr(existing, "api_base", None) if existing else None
            key_env = effective_key_env or (getattr(existing, "api_key_env", None) if existing else None)
            effective_key_env = key_env
            config.set_route(alias, upstream_model, api_key_env=key_env, api_base=api_base, **params)
        elif hasattr(gateway, "switch_route"):
            # An externally running gateway can still be switched safely via
            # its public restart operation.
            existing = getattr(config, "routes", {}).get(alias) if config is not None else None
            key_env = effective_key_env or (getattr(existing, "api_key_env", None) if existing else None)
            effective_key_env = key_env
            route_kwargs: dict[str, Any] = {"api_key_env": key_env, "restart": True}
            # Preserve provider-specific settings (api_base, timeout, headers,
            # deployment parameters, ...) when a shared/external gateway is
            # restarted.  Dropping them silently routes a request to a
            # different endpoint than the manifest claims.
            if existing is not None:
                existing_base = getattr(existing, "api_base", None)
                if existing_base:
                    route_kwargs["api_base"] = existing_base
                route_kwargs.update(dict(getattr(existing, "params", {}) or {}))
            gateway.switch_route(alias, upstream_model, **route_kwargs)
        else:
            # Reporting a route in the manifest without actually applying it
            # is worse than failing early: it silently invalidates comparisons
            # between Agent/framework runs.  Lightweight mock gateways must
            # therefore be used without --upstream-model (or implement the
            # same route-switching surface).
            raise RuntimeError(
                "gateway does not support dynamic route switching; provide a "
                "GatewayConfig or switch_route() implementation"
            )
        return {
            "alias": alias,
            "upstream_model": upstream_model,
            "provider_key_env": effective_key_env,
            "applied": True,
        }

    def _make_adapter(
        self,
        name: str,
        *,
        model: str | None = None,
        upstream_model: str | None = None,
        gateway: LiteLLMGateway | None = None,
        run_dir: Path | None = None,
        run_id: str | None = None,
    ) -> Any:
        if name in self.adapter_factory:
            factory = self.adapter_factory[name]
            # Custom factories may opt into context-aware construction.  Keep
            # zero-argument factories backwards compatible without swallowing a
            # TypeError raised *inside* a factory.
            # Keep the factory construction context compatible with existing
            # extensions. Built-in execution itself writes under logs/agent.
            agent_output = run_dir / "agent" if run_dir is not None else None
            return _call_factory(
                factory,
                model=model,
                gateway=gateway,
                name=name,
                run_dir=run_dir,
                run_id=run_id,
                output_dir=agent_output,
                path=agent_output,
            )
        options = dict(self.adapter_options.get(name, {}))
        if name == "webvoyager":
            options.setdefault("api_model", model or ("chat-gpt" if gateway else None))
            options.setdefault("api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("api_key", _gateway_key(gateway))
            return WebVoyagerAdapter(
                **options,
            )
        if name == "browseruse":
            options.setdefault("llm_provider", "openai" if gateway else "browser_use")
            options.setdefault("llm_model", model or ("chat-gpt" if gateway else None))
            # Keep the provider model visible to Browser-use.  The public
            # alias can be provider-neutral (for example ``chat-gpt``) while
            # the gateway routes it to DeepSeek, whose response-format
            # compatibility differs from OpenAI.
            options.setdefault("upstream_model", upstream_model)
            options.setdefault("api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("api_key", _gateway_key(gateway))
            return BrowserUseAdapter(
                **options,
            )
        if name == "mock":
            options.setdefault("gateway_url", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("gateway_key", _gateway_key(gateway))
            options.setdefault("model", model or "mock-model")
            options.setdefault("run_id", run_id)
            return MockAgentAdapter(**options)
        if name == "manual":
            return ManualAdapter(**options)
        if name == "skyvern":
            options.setdefault("api_key", os.environ.get("SKYVERN_API_KEY"))
            options.setdefault("llm_model", model or ("chat-gpt" if gateway else None))
            options.setdefault("llm_api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("llm_api_key", _gateway_key(gateway))
            return SkyvernAdapter(**options)
        if name == "agente":
            options.setdefault("llm_model", model or ("chat-gpt" if gateway else None))
            options.setdefault("llm_api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("llm_api_key", _gateway_key(gateway))
            return AgentEAdapter(**options)
        if name == "autogen":
            options.setdefault("llm_model", model or "gpt-4o")
            capability_model = upstream_model
            if capability_model is None and gateway is not None:
                config = getattr(gateway, "config", None)
                route = getattr(config, "routes", {}).get(model) if config is not None and model else None
                capability_model = getattr(route, "model", None)
            options.setdefault("upstream_model", capability_model)
            options.setdefault("api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("api_key", _gateway_key(gateway))
            return AutoGenAdapter(**options)
        raise ValueError(f"unsupported agent: {name}")

    def _make_sniffer(self, run_dir: Path) -> TrafficSniffer | None:
        if not self.use_network_probe or not self.collect_l1:
            return None
        if self.sniffer_factory:
            network_dir = run_dir / "logs" / "network"
            return _call_factory(
                self.sniffer_factory,
                output_dir=network_dir,
                network_dir=network_dir,
                path=network_dir,
                run_dir=run_dir,
            )
        return TrafficSniffer(run_dir / "logs" / "network", backend="tcpdump", interface="any")

    def run_once(
        self,
        url: str,
        prompt: str,
        *,
        agent_name: str = "webvoyager",
        model: str | None = None,
        run_id: str | None = None,
        output_dir: Path | str | None = None,
        start_gateway: bool = True,
        keep_sandbox: bool = False,
        upstream_model: str | None = None,
        provider_key_env: str | None = None,
        route_alias: str | None = None,
        agent_timeout: float | None = None,
        overwrite: bool = False,
    ) -> CycleResult:
        """Run one complete cycle and always clean up resources."""

        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if model and route_alias and model != route_alias:
            raise ValueError(
                "model and route_alias must match: the Agent must request the "
                "same public alias that the gateway rewrites"
            )
        # Resolve the public alias once and pass it consistently to route
        # configuration, the adapter, and the manifest.  Previously a custom
        # route_alias could be applied to the gateway while the Agent still
        # requested the default model, producing a misleading successful run.
        effective_model = model or route_alias
        if effective_model is None and agent_name == "mock":
            effective_model = "mock-model"
        elif effective_model is None and (
            self.gateway is not None or self.gateway_config is not None or self.gateway_factory is not None
        ):
            effective_model = self._default_route_alias(agent_name, model)
        identifier, run_dir = (
            self._new_run_dir(run_id, agent_name=agent_name, model=effective_model)
            if output_dir is None
            else (run_id or uuid.uuid4().hex[:12], Path(output_dir).expanduser().resolve())
        )
        _validate_run_id(identifier)
        if output_dir is not None and run_dir.exists() and not run_dir.is_dir():
            raise NotADirectoryError(f"output path is not a directory: {run_dir}")
        if output_dir is not None and run_dir.exists() and any(run_dir.iterdir()):
            if not overwrite:
                raise FileExistsError(
                    f"output directory is not empty: {run_dir}; choose a new directory or pass overwrite=True"
                )
            _clear_managed_run_artifacts(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / FINGERPRINT_DIR).mkdir(parents=True, exist_ok=True)
        (run_dir / ARTIFACT_DIR).mkdir(parents=True, exist_ok=True)
        requested_url = url
        started_at = datetime.now(timezone.utc).isoformat()
        gateway = None
        gateway_started_here = False
        route_metadata: dict[str, Any] | None = None
        sniffer = None
        capture: CaptureSession | None = None
        agent_result: AgentResult | None = None
        requires_verification = agent_name in VERIFIED_AGENTS
        verification: CompletionResult | None = None
        status = "failed"
        error: str | None = None
        interrupted = False
        sandbox = None
        sandbox_started_here = False
        trace_store: TraceStore | None = None
        cleanup_errors: list[str] = []
        capture_result: CaptureResult | None = None
        deferred_exception: BaseException | None = None
        trace_finalization_attempted = False

        def note_cleanup_failure(label: str, exc: BaseException) -> None:
            """Record cleanup failures without skipping the remaining teardown."""

            nonlocal interrupted, deferred_exception
            cleanup_errors.append(f"{label}: {type(exc).__name__}: {exc}")
            if isinstance(exc, KeyboardInterrupt):
                interrupted = True
            elif isinstance(exc, SystemExit) and deferred_exception is None:
                deferred_exception = exc

        try:
            if self.start_sandbox:
                sandbox, sandbox_started_here = self._ensure_sandbox_server(identifier)
                # Reuse one server across a matrix, but isolate each run's
                # event namespace and clear stale events first.
                sandbox.server.run_id = identifier
                sandbox.trace_store.clear(identifier)
                sandbox.server.completion_verifier.clear(identifier)
                trace_store = sandbox.trace_store
                url = _resolve_task_url(url, sandbox.base_url)
            gateway = self._make_gateway(run_dir)
            if upstream_model and gateway is None:
                raise ValueError("upstream_model requires a LiteLLM-compatible gateway")
            if gateway:
                route_metadata = self._configure_gateway_route(
                    gateway,
                    agent_name=agent_name,
                    model=effective_model,
                    upstream_model=upstream_model,
                    provider_key_env=provider_key_env,
                    route_alias=route_alias,
                )
            if gateway and not start_gateway and not _is_running(gateway):
                raise RuntimeError("start_gateway=False requires the supplied gateway to be running")
            if gateway and start_gateway and not _is_running(gateway):
                gateway_started_here = True
                gateway.start()
            sniffer = self._make_sniffer(run_dir)
            if sniffer:
                capture = sniffer.start()
            adapter = self._make_adapter(
                agent_name,
                model=effective_model,
                upstream_model=upstream_model,
                gateway=gateway,
                run_dir=run_dir,
                run_id=identifier,
            )
            if requires_verification and hasattr(adapter, "check"):
                # Framework verdicts are diagnostic; verify page completion
                # after the browser's final uploads have settled.
                adapter.check = False
            effective_timeout = self.agent_timeout if agent_timeout is None else agent_timeout
            if effective_timeout is not None:
                if effective_timeout <= 0:
                    raise ValueError("agent_timeout must be positive or None")
                # Built-in and most custom adapters use the common timeout
                # attribute.  A factory remains free to enforce its own policy.
                if hasattr(adapter, "timeout"):
                    adapter.timeout = effective_timeout
            # An explicit model and gateway URL are passed to concrete adapters
            # by the caller's factory.  Keep the generic adapter contract small.
            # A mitmproxy capture is an explicit proxy mode; pass its address
            # through the adapter when it supports the conventional attribute.
            if sniffer and getattr(sniffer, "proxy_url", None):
                proxy_url = sniffer.proxy_url
                # Built-in adapters expose this attribute; custom adapters can
                # optionally consume it through a setter.  Fail explicitly if
                # an extension cannot accept the proxy; otherwise the run
                # would be reported as complete while bypassing mitmproxy.
                try:
                    setattr(adapter, "proxy_url", proxy_url)
                except (AttributeError, TypeError) as exc:
                    setter = getattr(adapter, "set_proxy_url", None)
                    if callable(setter):
                        setter(proxy_url)
                    else:
                        raise RuntimeError(
                            "adapter does not expose proxy_url/set_proxy_url; "
                            "cannot run with the mitmproxy network backend"
                        ) from exc
            agent_result = adapter.run_task(url, prompt, run_dir / "logs" / "agent")
            if not isinstance(agent_result, AgentResult):
                raise TypeError("adapter.run_task must return AgentResult")
            completed = agent_result.execution_success if requires_verification else agent_result.success
            status = "success" if completed else "failed"
            if not completed:
                if not agent_result.execution_success:
                    error = error or f"agent returned non-zero code {agent_result.returncode}"
                else:
                    error = error or "agent did not report task success"
            # A proxy that dies just after serving the final Agent request
            # must not make the run look healthy.  Check only gateways owned
            # by this cycle; a caller-supplied shared gateway may have an
            # intentional independent lifecycle.
            if gateway and gateway_started_here and not _is_running(gateway):
                status = "failed"
                error = error or "gateway exited before cycle completion"
        except AgentInterruptedError as exc:
            interrupted = True
            error = f"{type(exc).__name__}: {exc}"
            status = "failed"
            if agent_result is None:
                agent_result = exc.result
        except (KeyboardInterrupt, SystemExit) as exc:
            # Keep the lifecycle deterministic even when a custom adapter
            # raises a process-level interrupt instead of returning an
            # AgentResult.  Built-in adapters convert interrupts to
            # AgentExecutionError, but this guard protects extension points.
            interrupted = isinstance(exc, KeyboardInterrupt)
            error = f"{type(exc).__name__}: {exc or 'interrupted'}"
            status = "failed"
            if agent_result is None:
                agent_result = getattr(exc, "result", None)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if agent_result is None:
                agent_result = getattr(exc, "result", None)
        finally:
            # A manually operated system-browser tab outlives the adapter. Ask
            # the injected monitor to stop, retransmit from the last server ACK,
            # and explicitly confirm its final sequence before exporting JSON.
            # Legacy/custom probes do not register as protocol-v2 sessions and
            # continue to use the short grace period below.
            if (
                agent_name == "manual"
                and trace_store is not None
                and self.inject_monitor
                and (self.collect_l2 or self.collect_l3)
            ):
                try:
                    expected_sessions = trace_store.request_finalize(identifier)
                    if expected_sessions:
                        trace_finalization_attempted = True
                        print(
                            f"Finalizing browser trace for {len(expected_sessions)} session(s)...",
                            flush=True,
                        )
                        finalization = trace_store.wait_for_finalization(
                            identifier,
                            expected_sessions,
                            self.trace_finalize_timeout_seconds,
                        )
                        if finalization["complete"]:
                            print("Browser trace upload confirmed.", flush=True)
                        else:
                            missing = ", ".join(finalization["missing"])
                            cleanup_errors.append(
                                "browser trace finalization timed out; unconfirmed session(s): " + missing
                            )
                except BaseException as exc:
                    note_cleanup_failure("browser trace finalization failed", exc)
            if capture:
                try:
                    capture_result = capture.stop()
                    if not getattr(capture_result, "success", True):
                        status = "failed"
                        cleanup_errors.append("network capture exited unsuccessfully")
                    if not Path(capture_result.output_path).is_file():
                        cleanup_errors.append(f"network artifact missing: {capture_result.output_path}")
                except BaseException as exc:
                    capture_result = None
                    note_cleanup_failure("capture stop failed", exc)
            else:
                capture_result = None
            if gateway and gateway_started_here:
                try:
                    gateway.stop()
                except BaseException as exc:
                    note_cleanup_failure("gateway stop failed", exc)
            # Browser fetch(..., {keepalive:true}) may still be completing when
            # the child exits.  Give the HTTP handler a short, configurable
            # grace period before shutting down the collector.
            if trace_store and self.trace_grace_seconds and not trace_finalization_attempted:
                try:
                    time.sleep(self.trace_grace_seconds)
                except BaseException as exc:
                    note_cleanup_failure("trace grace wait failed", exc)
            if (sandbox_started_here or self._sandbox_owned) and self._sandbox and not keep_sandbox:
                try:
                    self.stop_sandbox_server()
                except BaseException as exc:
                    note_cleanup_failure("sandbox stop failed", exc)

        if requires_verification:
            verification = (
                sandbox.server.completion_verifier.result(identifier)
                if sandbox is not None else CompletionResult()
            )
            if agent_result is not None:
                agent_result.verification = verification
            if not verification.success:
                status = "failed"
                error = error or verification.reason

        try:
            ui_trace = self._write_ui_trace(run_dir, identifier, trace_store)
        except BaseException as exc:
            note_cleanup_failure("UI trace write failed", exc)
            status = "failed"
            ui_trace = run_dir / FINGERPRINT_DIR / FINGERPRINT_FILES["l3"]
            ui_trace.parent.mkdir(parents=True, exist_ok=True)
            ui_trace.write_text(
                json.dumps(
                    {
                        "schema": "agent-fingerprint-ui-trace/v1",
                        "run_id": identifier,
                        "event_count": 0,
                        "events": [],
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        if (
            agent_name == "manual"
            and trace_store is not None
            and self.inject_monitor
            and (self.collect_l2 or self.collect_l3)
        ):
            manual_trace = trace_store.export(identifier)
            if not manual_trace.get("event_count"):
                status = "failed"
                error = error or (
                    "manual session captured no browser events; open the printed sandbox URL, "
                    "interact with the page, then press Enter"
                )
        try:
            fingerprint_paths = self._write_fingerprints(
                run_dir,
                identifier,
                ui_trace,
                capture_result,
                agent_result,
                started_at,
                gateway,
            )
        except BaseException as exc:
            note_cleanup_failure("fingerprint write failed", exc)
            fingerprint_paths = {"l3": ui_trace}

        if cleanup_errors:
            error = "; ".join([part for part in ([error] if error else []) + cleanup_errors])
            status = "failed"
        reported_model = effective_model
        try:
            organized = _organize_run_files(
                run_dir,
                agent_result=agent_result,
                capture_result=capture_result,
                gateway=gateway,
            )
        except BaseException as exc:
            note_cleanup_failure("run artifact organization failed", exc)
            error = "; ".join(part for part in ([error] if error else []) + [f"artifact organization failed: {exc}"])
            status = "failed"
            organized = {"native_result": {}, "artifacts": {}, "logs": {}}
        try:
            manifest = self._write_manifest(
                run_dir,
                identifier,
                url,
                prompt,
                agent_name,
                reported_model,
                status,
                error,
                started_at,
                agent_result,
                capture_result,
                ui_trace,
                gateway,
                requested_url=requested_url,
                route_metadata=route_metadata,
                cleanup_errors=cleanup_errors,
                fingerprint_paths=fingerprint_paths,
                organized=organized,
                verification=verification,
            )
        except BaseException as exc:
            # Artifact bookkeeping must never erase the diagnostic result of a
            # failed Agent.  Fall back to a minimal but valid manifest.
            note_cleanup_failure("manifest write failed", exc)
            error = "; ".join(part for part in ([error] if error else []) + [f"manifest write failed: {exc}"])
            status = "failed"
            manifest = _fallback_manifest(
                run_dir,
                identifier,
                requested_url,
                url,
                prompt,
                agent_name,
                reported_model,
                status,
                error,
                started_at,
            )
        result = CycleResult(identifier, run_dir, manifest, agent_result)
        if output_dir is None:
            _append_run_index(self.output_root, run_dir, manifest)
        if deferred_exception is not None:
            raise deferred_exception
        if interrupted:
            # The manifest is durable and cleanup has completed, but an
            # operator interrupt must still stop a matrix/CLI instead of
            # silently proceeding to the next Agent.
            raise KeyboardInterrupt
        return result

    def run_matrix(
        self,
        tasks: Iterable[Mapping[str, Any]],
        agents: Sequence[str],
    ) -> list[CycleResult]:
        """Run every task × Agent combination with unique output directories."""

        if isinstance(agents, str):
            agents = [agents]
        if not agents:
            if self._sandbox_owned:
                self.stop_sandbox_server()
            return []
        # Keep one sandbox process alive for the matrix, matching the intended
        # “start infrastructure once, then iterate” lifecycle.
        started_for_matrix = False
        owned_before_matrix = self._sandbox_owned
        if self.start_sandbox and self._sandbox is None:
            self.start_sandbox_server("matrix")
            started_for_matrix = True
        results = []
        try:
            for task in tasks:
                task_url = task.get("url", task.get("web"))
                task_prompt = task.get("prompt", task.get("ques"))
                if task_url is None or task_prompt is None:
                    raise ValueError("each task requires url/prompt (or web/ques)")
                for agent_name in agents:
                    results.append(
                        self.run_once(
                            str(task_url),
                            str(task_prompt),
                            agent_name=agent_name,
                            model=task.get("model"),
                            keep_sandbox=True,
                            upstream_model=task.get("upstream_model"),
                            provider_key_env=task.get("provider_key_env"),
                            route_alias=task.get("route_alias"),
                            agent_timeout=task.get("agent_timeout"),
                        )
                    )
        finally:
            if started_for_matrix or owned_before_matrix:
                active_exception = sys.exc_info()[0] is not None
                try:
                    self.stop_sandbox_server()
                except Exception as exc:
                    message = f"sandbox stop failed after matrix: {exc}"
                    if results:
                        # Preserve already-written results while making the
                        # cleanup failure visible to downstream evaluators.
                        for cycle in results:
                            cycle.manifest.setdefault("cleanup_errors", []).append(message)
                            cycle.manifest["status"] = "failed"
                            cycle.manifest["error"] = "; ".join(
                                part
                                for part in ([cycle.manifest.get("error")] if cycle.manifest.get("error") else []) + [
                                    message
                                ]
                            )
                            try:
                                manifest_path = cycle.output_dir / "logs" / "manifest.json"
                                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                                manifest_path.write_text(
                                    json.dumps(cycle.manifest, ensure_ascii=False, indent=2, default=str),
                                    encoding="utf-8",
                                )
                            except Exception:
                                pass
                    elif not active_exception:
                        raise RuntimeError(message) from exc
        return results

    def _write_ui_trace(self, run_dir: Path, run_id: str, trace_store: TraceStore | None = None) -> Path:
        if trace_store:
            trace = trace_store.export(run_id)
        else:
            trace = {"schema": "agent-fingerprint-ui-trace/v1", "run_id": run_id, "event_count": 0, "events": []}
        if not self.collect_l3:
            samples = [
                event.get("static_fingerprint")
                for event in trace.get("events", [])
                if isinstance(event, dict) and isinstance(event.get("static_fingerprint"), dict)
            ]
            trace = {
                "schema": "agent-fingerprint-browser-dynamic/v1",
                "run_id": run_id,
                "enabled": False,
                "event_count": 0,
                "events": [],
            }
            if self.collect_l2 and samples:
                trace["static_fingerprint"] = samples[0]
        else:
            trace["enabled"] = True
        path = run_dir / FINGERPRINT_DIR / FINGERPRINT_FILES["l3"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_fingerprints(
        self,
        run_dir: Path,
        run_id: str,
        ui_trace: Path,
        capture_result: CaptureResult | None,
        agent_result: AgentResult | None,
        started_at: str,
        gateway: Any | None,
    ) -> dict[str, Path]:
        """Write the four stable raw fingerprint documents."""

        logs_dir = run_dir / "logs"
        trace = _read_json(ui_trace, {})
        static_samples = []
        if self.collect_l2:
            if isinstance(trace.get("static_fingerprint"), dict):
                static_samples.append(trace["static_fingerprint"])
            for event in trace.get("events", []):
                sample = event.get("static_fingerprint") if isinstance(event, dict) else None
                if isinstance(sample, dict):
                    static_samples.append(sample)

        capture_document: dict[str, Any] = {
            "schema": "agent-fingerprint-http-tls-raw/v1",
            "run_id": run_id,
            "enabled": self.collect_l1 and self.use_network_probe,
            "capture": None,
        }
        if self.collect_l1 and self.use_network_probe and capture_result is not None:
            capture_document["capture"] = capture_result.to_dict()
            capture_path = Path(capture_result.output_path)
            # Preserve the complete capture, but classify the binary payload as
            # an artifact rather than mixing it with the four JSON fingerprints.
            archived_pcap = run_dir / ARTIFACT_DIR / "network" / "traffic.pcap"
            archived_pcap.parent.mkdir(parents=True, exist_ok=True)
            if capture_path.is_file() and capture_path.resolve() != archived_pcap.resolve():
                shutil.copy2(capture_path, archived_pcap)
            capture_document["capture_file"] = _artifact_name(archived_pcap, run_dir)
            capture_document["capture_size_bytes"] = capture_path.stat().st_size if capture_path.is_file() else None

        static_document = {
            "schema": "agent-fingerprint-browser-static-raw/v1",
            "run_id": run_id,
            "enabled": self.collect_l2,
            "sample_count": len(static_samples),
            "samples": static_samples,
        }

        gateway_events: list[dict[str, Any]] = []
        if self.collect_l4:
            latency_path = _safe_path_call(gateway, "latency_log_path") if gateway else None
            if latency_path and latency_path.is_file():
                gateway_events = _read_jsonl_path(latency_path)
            else:
                gateway_events = _read_jsonl_files(logs_dir, "gateway_events.jsonl")
        agent_document: dict[str, Any] = {
            "schema": "agent-fingerprint-agent-llm-raw/v1",
            "run_id": run_id,
            "enabled": self.collect_l4,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "agent_result": _compact_agent_result(agent_result) if self.collect_l4 and agent_result else None,
            "llm_events": gateway_events,
        }

        documents = {
            "l1": capture_document,
            "l2": static_document,
            "l4": agent_document,
        }
        paths = {"l3": ui_trace}
        for level, document in documents.items():
            path = run_dir / FINGERPRINT_DIR / FINGERPRINT_FILES[level]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            paths[level] = path
        return paths

    def _write_manifest(
        self,
        run_dir: Path,
        run_id: str,
        url: str,
        prompt: str,
        agent_name: str,
        model: str | None,
        status: str,
        error: str | None,
        started_at: str,
        agent_result: AgentResult | None,
        capture_result: CaptureResult | None,
        ui_trace: Path,
        gateway: LiteLLMGateway | None,
        requested_url: str | None = None,
        route_metadata: dict[str, Any] | None = None,
        cleanup_errors: Sequence[str] = (),
        fingerprint_paths: Mapping[str, Path] | None = None,
        organized: Mapping[str, Any] | None = None,
        verification: CompletionResult | None = None,
    ) -> dict[str, Any]:
        organized = dict(organized or {})
        native_result = organized.get("native_result")
        if not isinstance(native_result, Mapping):
            native_result = {}
        output = native_result.get("output")
        if output is None:
            output = native_result.get("answer")
        if output is None and agent_result and agent_result.stdout:
            output = agent_result.stdout.strip()
        framework_status = native_result.get("task_status") or native_result.get("status")
        framework_success = native_result.get("task_success")
        if not isinstance(framework_success, bool):
            framework_success = agent_result.task_success if agent_result else None
        manifest: dict[str, Any] = {
            "schema": "agent-fingerprint-run/v2",
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error": error,
            "task": {"url": url, "prompt": prompt},
            "agent": {"name": agent_name, "model": model},
            "outcome": {
                "success": verification.success if verification is not None else framework_success,
                "verification": verification.to_dict() if verification is not None else None,
                "framework_success": framework_success,
                "framework_status": framework_status,
                "framework_failure_reason": native_result.get("failure_reason"),
                "output": output,
                "failure_reason": (
                    None if verification.success else verification.reason
                ) if verification is not None else native_result.get("failure_reason"),
                "step_count": native_result.get("step_count"),
            },
            "execution": _compact_agent_result(agent_result),
            "fingerprints": {
                level: _artifact_name(path, run_dir) for level, path in (fingerprint_paths or {"l3": ui_trace}).items()
            },
            "artifacts": organized.get("artifacts", {}),
            "logs": organized.get("logs", {}),
        }
        if requested_url is not None and requested_url != url:
            manifest["task"]["requested_url"] = requested_url
        if cleanup_errors:
            manifest["cleanup_errors"] = list(cleanup_errors)
        if capture_result:
            manifest["network"] = {
                "backend": capture_result.backend,
                "success": capture_result.success,
                "started_at": capture_result.started_at,
                "stopped_at": capture_result.stopped_at,
                "duration_seconds": capture_result.duration_seconds,
                "error": capture_result.error,
            }
        if gateway:
            manifest["gateway"] = {
                "base_url": getattr(gateway, "base_url", None),
                "latency_event_count": organized.get("gateway_event_count", 0),
            }
            if route_metadata:
                manifest["gateway"]["route"] = route_metadata
            config_sha256 = organized.get("gateway_config_sha256")
            if config_sha256:
                manifest["gateway"]["config_sha256"] = config_sha256
        manifest["files"] = _canonical_run_files(run_dir, include_manifest=True)
        path = run_dir / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return manifest


def _call_factory(factory: Callable[..., Any], **context: Any) -> Any:
    """Call a user factory with only the arguments it declares.

    This supports old ``lambda: ...`` test factories and newer factories such
    as ``lambda run_dir: ...``/``lambda model, gateway: ...``.  Importantly,
    exceptions raised by the factory itself are not mistaken for a signature
    mismatch.
    """

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(**context)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return factory(**context)
    positional: list[Any] = []
    keyword: dict[str, Any] = {}
    for parameter in parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        has_value = parameter.name in context and context[parameter.name] is not None
        value = context.get(parameter.name)
        if not has_value and parameter.default is inspect.Parameter.empty:
            # Preserve the historical one-positional-argument path API even
            # when the callable also declares keyword-only context (for
            # example ``factory(path, *, model=None)``).
            value = context.get("path", context.get("output_dir", context.get("run_dir")))
            has_value = value is not None
        if not has_value:
            continue
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            positional.append(value)
        elif parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD and parameter.default is inspect.Parameter.empty:
            positional.append(value)
        else:
            keyword[parameter.name] = value
    return factory(*positional, **keyword)


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(
            "run_id must be 1-128 characters of letters, digits, '.', '_' or '-' "
            "and must not contain path separators"
        )


def _short_name(value: str) -> str:
    """Convert an agent/model label to a filesystem-safe short identifier."""
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").lower()
    return text[:16] or "agent"


def _short_model(value: str) -> str:
    raw = str(value).lower()
    if "deepseek" in raw:
        return "deepseek"
    if "claude" in raw:
        return "claude"
    if "gpt" in raw or "chatgpt" in raw:
        return "gpt"
    text = _short_name(value)
    # Keep identifiers readable while avoiding overly long directory names.
    return text[:24] or "model"


def _infer_provider_key_env(upstream_model: str | None) -> str | None:
    """Infer the conventional provider credential variable from a model id."""

    value = (upstream_model or "").strip().lower()
    provider = value.split("/", 1)[0] if "/" in value else value.split("-", 1)[0]
    return {
        "deepseek": "DEEPSEEK_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "RELAY_OPENAI_API_KEY",
    }.get(provider)


def _resolve_task_url(value: str, sandbox_base_url: str) -> str:
    """Resolve a sandbox-relative URL and reject ambiguous/malformed schemes."""

    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must use http:// or https://")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("url contains an invalid port") from exc
        return value
    if parsed.netloc or value.startswith("//"):
        raise ValueError("scheme-relative URLs are not accepted; use an absolute http(s) URL")
    return sandbox_base_url.rstrip("/") + "/" + value.lstrip("/")


def _fallback_manifest(
    run_dir: Path,
    run_id: str,
    requested_url: str,
    resolved_url: str,
    prompt: str,
    agent_name: str,
    model: str | None,
    status: str,
    error: str,
    started_at: str,
) -> dict[str, Any]:
    manifest = {
        "schema": "agent-fingerprint-run/v2",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error": error,
        "task": {"url": resolved_url, "requested_url": requested_url, "prompt": prompt},
        "agent": {"name": agent_name, "model": model},
        "outcome": {
            "success": False,
            "framework_status": None,
            "output": None,
            "failure_reason": error,
            "step_count": None,
        },
        "execution": None,
        "fingerprints": {},
        "artifacts": {},
        "logs": {},
        "files": ["manifest.json"],
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest


def _is_running(resource: Any) -> bool:
    value = getattr(resource, "running", None)
    if isinstance(value, bool):
        return value
    checker = getattr(resource, "is_running", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            pass
    process = getattr(resource, "process", None)
    if process is not None and hasattr(process, "poll"):
        try:
            return process.poll() is None
        except Exception:
            pass
    return bool(getattr(resource, "started", False))


def _gateway_key(gateway: Any) -> str | None:
    if gateway is None:
        return None
    getter = getattr(gateway, "auth_key", None)
    if callable(getter):
        try:
            getter = getter()
        except Exception:
            getter = None
    if getter:
        return str(getter)
    env_name = getattr(gateway, "master_key_env", None)
    runtime = getattr(gateway, "_runtime_env", {})
    return (runtime.get(env_name) if env_name else None) or (os.environ.get(env_name) if env_name else None)


def _safe_path_call(resource: Any, method_name: str) -> Path | None:
    method = getattr(resource, method_name, None)
    if not callable(method):
        return None
    try:
        value = method()
    except Exception:
        return None
    return Path(value).expanduser().resolve() if value else None


def _safe_path_attr(resource: Any, parent_name: str, child_name: str) -> Path | None:
    parent = getattr(resource, parent_name, None)
    if parent is None:
        return None
    # ``output_dir`` is a pathlib.Path in the built-in gateways.  Calling
    # ``getattr(parent, "litellm.stdout.log")`` (the old implementation)
    # can never work because dots are not attribute separators; compose the
    # child path explicitly and also accept objects (such as GatewayConfig)
    # that expose a simple path attribute.
    try:
        if isinstance(parent, (str, os.PathLike, Path)):
            return (Path(parent).expanduser().resolve() / child_name).resolve()
        # GatewayConfig (and a few custom gateway objects) expose a ``path``
        # attribute rather than being path-like themselves.
        value = getattr(parent, child_name, None)
        return Path(value).expanduser().resolve() if value is not None else None
    except (TypeError, ValueError, OSError):
        return None


def _compact_agent_result(result: AgentResult | None) -> dict[str, Any] | None:
    """Return reproducibility metadata without embedding an inline runner."""

    if result is None:
        return None
    command = [str(item) for item in result.command]
    digest = hashlib.sha256(
        json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    launcher = result.adapter
    if command:
        launcher = Path(command[0]).name or result.adapter
    return {
        "adapter": result.adapter,
        "launcher": launcher,
        "command_sha256": digest,
        "returncode": result.returncode,
        "duration_seconds": result.duration_seconds,
        "task_success": result.task_success,
        "task_status": result.task_status,
        "execution_success": result.execution_success,
    }


def _read_jsonl_path(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return records
    for line in lines:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _archive_nonempty_log(source: Path | None, destination: Path) -> str | None:
    if source is None:
        return None
    source = Path(source)
    try:
        if not source.is_file() or source.stat().st_size == 0:
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return str(destination)
    except OSError:
        return None


def _unique_destination(directory: Path, preferred: str) -> Path:
    candidate = directory / preferred
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while (directory / f"{stem}-{index:03d}{suffix}").exists():
        index += 1
    return directory / f"{stem}-{index:03d}{suffix}"


def _archive_numbered_files(sources: Iterable[Path], destination: Path, prefix: str) -> list[Path]:
    files = sorted(
        (Path(path) for path in sources if Path(path).is_file() and not Path(path).is_symlink()),
        key=lambda path: path.name,
    )
    archived: list[Path] = []
    if not files:
        return archived
    destination.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(files, start=1):
        suffix = source.suffix.lower()
        filename = f"{prefix}{suffix}" if len(files) == 1 else f"{prefix}-{index:03d}{suffix}"
        target = _unique_destination(destination, filename)
        shutil.copy2(source, target)
        archived.append(target)
    return archived


def _organize_run_files(
    run_dir: Path,
    *,
    agent_result: AgentResult | None,
    capture_result: CaptureResult | None,
    gateway: Any | None,
) -> dict[str, Any]:
    """Collapse runtime staging files into the v2 logs/artifacts surface."""

    logs_dir = run_dir / "logs"
    artifacts_dir = run_dir / ARTIFACT_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    native_result: dict[str, Any] = {}
    artifact_map: dict[str, Any] = {}
    log_map: dict[str, str] = {}

    agent_dir = Path(agent_result.output_dir) if agent_result else logs_dir / "agent"
    if agent_result:
        _ensure_agent_logs(agent_result)
    result_path = agent_dir / "result.json"
    value = _read_json(result_path, {})
    if isinstance(value, dict):
        native_result = value

    # Create the framework-neutral interaction view before staging files are
    # cleaned up. Browser probe events remain in L3 and are not duplicated.
    latency_path = _safe_path_call(gateway, "latency_log_path") if gateway else None
    interaction_events = _read_jsonl_path(latency_path) if latency_path and latency_path.is_file() else []
    actions: list[dict[str, Any]] = []
    trace = native_result.get("trace") if isinstance(native_result, dict) else None
    if isinstance(trace, list):
        for item in trace:
            if not isinstance(item, dict) or not item.get("action"):
                continue
            actions.append({
                "step": len(actions) + 1,
                "action": item.get("action"),
                "target": item.get("target"),
                "arguments": item.get("arguments", {"argument_length": item.get("argument_length")}),
                "source": "framework",
                "timestamp": item.get("timestamp"),
            })
    stdout_path = agent_dir / "stdout.log"
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.is_file() else ""
    for match in re.finditer(r"(?:action_type['\"]?\s*[:=]\s*['\"]|Executing tool\s+|Calling tool\s+)([A-Za-z_][A-Za-z0-9_-]*)", stdout_text):
        name = match.group(1)
        if not any(item.get("action") == name for item in actions):
            actions.append({"step": len(actions) + 1, "action": name, "target": None, "arguments": {}, "source": "framework_log", "timestamp": None})
    # WebVoyager stores its conversation as interact_messages.json. Extract
    # only the declared action/tool name; the full conversation remains in the
    # framework artifact when available.
    for message_path in agent_dir.rglob("interact_messages.json"):
        try:
            messages = json.loads(message_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(messages, list):
            continue
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                continue
            action_match = re.search(r"(?:Action|action)\s*:\s*(?:[A-Z]+\s*)?([A-Za-z_][A-Za-z0-9_-]*)", content)
            if action_match:
                name = action_match.group(1)
                if not any(item.get("action") == name for item in actions):
                    actions.append({"step": len(actions) + 1, "action": name, "target": None, "arguments": {}, "source": "framework_trace", "timestamp": None})
    final_value = native_result.get("output") if isinstance(native_result, dict) else None
    if final_value is None and agent_result and agent_result.stdout:
        final_value = agent_result.stdout.strip()
    verification = agent_result.verification if agent_result else None
    framework_status = native_result.get("task_status") if isinstance(native_result, dict) else None
    final_status = (
        ("completed" if verification.success else verification.status)
        if verification is not None else framework_status
    )
    interaction_path = logs_dir / "agent_interactions.json"
    interaction_path.write_text(json.dumps({
        "schema": "agent-fingerprint-interactions/v1",
        "run_id": run_dir.name,
        "llm_interactions": interaction_events,
        "agent_actions": actions,
        "final_output": {
            "value": final_value,
            "status": final_status or ("completed" if agent_result and agent_result.task_success is True else "unknown"),
            "framework_status": framework_status,
        },
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log_map["agent_interactions"] = _artifact_name(interaction_path, run_dir)

    if agent_dir.is_dir():
        recording_sources = list((agent_dir / "skyvern_artifacts" / "recordings").glob("*"))
        screenshot_sources = list((agent_dir / "skyvern_artifacts" / "screenshots").glob("*"))
        screenshot_sources.extend((agent_dir / "screenshots").glob("*"))
        recordings = _archive_numbered_files(
            recording_sources, artifacts_dir / "recordings", "recording"
        )
        screenshots = _archive_numbered_files(
            screenshot_sources, artifacts_dir / "screenshots", "step"
        )
        if recordings:
            artifact_map["recordings"] = [_artifact_name(path, run_dir) for path in recordings]
        if screenshots:
            artifact_map["screenshots"] = [_artifact_name(path, run_dir) for path in screenshots]

        download_dir = agent_dir / "downloads"
        archived_downloads: list[Path] = []
        if download_dir.is_dir():
            for source in sorted(download_dir.rglob("*")):
                if not source.is_file() or source.is_symlink():
                    continue
                target_dir = artifacts_dir / "downloads" / source.relative_to(download_dir).parent
                target_dir.mkdir(parents=True, exist_ok=True)
                target = _unique_destination(target_dir, source.name)
                shutil.copy2(source, target)
                archived_downloads.append(target)
        if archived_downloads:
            artifact_map["downloads"] = [_artifact_name(path, run_dir) for path in archived_downloads]

        ignored_names = {
            "stdout.log",
            "stderr.log",
            "adapter_result.json",
            "result.json",
            "response.json",
            "webvoyager_task.jsonl",
        }
        ignored_roots = {"skyvern_artifacts", "screenshots", "downloads", "__pycache__"}
        framework_files: list[Path] = []
        for source in sorted(agent_dir.rglob("*")):
            if not source.is_file() or source.is_symlink() or source.name in ignored_names:
                continue
            relative = source.relative_to(agent_dir)
            if relative.parts and relative.parts[0] in ignored_roots:
                continue
            target = artifacts_dir / "framework" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            framework_files.append(target)
        if framework_files:
            artifact_map["framework"] = [_artifact_name(path, run_dir) for path in framework_files]

        for key, filename in (("agent_stdout", "stdout.log"), ("agent_stderr", "stderr.log")):
            target = logs_dir / ("agent.stdout.log" if key.endswith("stdout") else "agent.stderr.log")
            archived = _archive_nonempty_log(agent_dir / filename, target)
            if archived:
                log_map[key] = _artifact_name(Path(archived), run_dir)

    if capture_result:
        for key, source, filename in (
            ("network_stdout", Path(capture_result.stdout_log), "network.stdout.log"),
            ("network_stderr", Path(capture_result.stderr_log), "network.stderr.log"),
        ):
            archived = _archive_nonempty_log(source, logs_dir / filename)
            if archived:
                log_map[key] = _artifact_name(Path(archived), run_dir)
        pcap = artifacts_dir / "network" / "traffic.pcap"
        if pcap.is_file():
            artifact_map["network_capture"] = _artifact_name(pcap, run_dir)

    latency_path = _safe_path_call(gateway, "latency_log_path") if gateway else None
    gateway_event_count = _count_jsonl(latency_path)
    config_path = _safe_path_attr(gateway, "config", "path") if gateway else None
    config_sha256 = None
    if config_path and config_path.is_file():
        try:
            config_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest()
        except OSError:
            config_sha256 = None
    if gateway:
        for key, source, filename in (
            ("gateway_stdout", _safe_path_attr(gateway, "output_dir", "litellm.stdout.log"), "gateway.stdout.log"),
            ("gateway_stderr", _safe_path_attr(gateway, "output_dir", "litellm.stderr.log"), "gateway.stderr.log"),
        ):
            archived = _archive_nonempty_log(source, logs_dir / filename)
            if archived:
                log_map[key] = _artifact_name(Path(archived), run_dir)

    # Runtime-only directories can contain copied callback source and Python
    # bytecode. All useful content has been normalized above.
    for path in (
        agent_dir,
        Path(capture_result.output_dir) if capture_result else None,
        run_dir / "mock_gateway",
        logs_dir / "mock_gateway",
        logs_dir / "gateway",
    ):
        if path is None:
            continue
        path = Path(path)
        try:
            path.relative_to(run_dir)
        except ValueError:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.is_symlink() or path.is_file():
            path.unlink()
    for path in (
        logs_dir / "litellm.config.yaml",
        logs_dir / "litellm.stdout.log",
        logs_dir / "litellm.stderr.log",
        logs_dir / "litellm_gateway.json",
        logs_dir / "gateway_events.jsonl",
        run_dir / "gateway_events.jsonl",
        run_dir / "agent",
        run_dir / "network",
        run_dir / "ui_trace.json",
        run_dir / "result.json",
    ):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    return {
        "native_result": native_result,
        "artifacts": artifact_map,
        "logs": log_map,
        "gateway_event_count": gateway_event_count,
        "gateway_config_sha256": config_sha256,
    }


def _canonical_run_files(run_dir: Path, *, include_manifest: bool = False) -> list[str]:
    paths: list[str] = []
    for root_name in (FINGERPRINT_DIR, ARTIFACT_DIR, "logs"):
        root = run_dir / root_name
        if not root.is_dir() or root.is_symlink():
            continue
        paths.extend(
            _artifact_name(path, run_dir)
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    if include_manifest:
        paths.append("manifest.json")
    return sorted(set(paths))


def _append_run_index(output_root: Path, run_dir: Path, manifest: Mapping[str, Any]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": manifest.get("run_id"),
        "path": str(run_dir.relative_to(output_root)),
        "status": manifest.get("status"),
        "started_at": manifest.get("started_at"),
        "agent": (manifest.get("agent") or {}).get("name"),
        "model": (manifest.get("agent") or {}).get("model"),
    }
    encoded = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(output_root / RUN_INDEX_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


def _count_jsonl(path: Path | None) -> int:
    if path is None or not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8") as stream:
            return sum(1 for line in stream if line.strip())
    except (OSError, UnicodeError):
        return 0


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _read_jsonl_files(root: Path, filename: str) -> list[dict[str, Any]]:
    """Read sanitized gateway event records from the run's log tree."""

    records: list[dict[str, Any]] = []
    if not root.is_dir():
        return records
    for path in sorted(root.rglob(filename)):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def _artifact_name(path: Path | None, run_dir: Path) -> str:
    if path is None:
        return ""
    path = Path(path).expanduser().resolve()
    try:
        return str(path.relative_to(run_dir.resolve()))
    except ValueError:
        return str(path)


def _clear_managed_run_artifacts(run_dir: Path) -> None:
    """Remove only paths owned by this pipeline before an explicit reuse.

    Unknown files are deliberately preserved.  This gives --overwrite useful
    fresh-log semantics without turning a typo in --output-dir into a broad
    recursive deletion.
    """

    managed_directories = (
        "artifacts",
        "logs/agent",
        "logs/network",
        "logs/mock_gateway",
        "logs/gateway",
        "agent",
        "network",
        "fingerprints",
    )
    managed_files = (
        "logs/manifest.json",
        "logs/agent.stdout.log",
        "logs/agent.stderr.log",
        "logs/network.stdout.log",
        "logs/network.stderr.log",
        "logs/gateway.stdout.log",
        "logs/gateway.stderr.log",
        "logs/gateway_events.jsonl",
        "gateway_events.jsonl",
        *FINGERPRINT_FILES.values(),
        "manifest.json",
        "ui_trace.json",
        "result.json",
    )
    for name in managed_directories:
        path = run_dir / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    for name in managed_files:
        path = run_dir / name
        if path.is_symlink() or path.is_file():
            path.unlink()


def _ensure_agent_logs(result: AgentResult) -> None:
    """Make the normalized log artifacts present for lightweight adapters too."""

    output_dir = Path(result.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout = output_dir / "stdout.log"
    stderr = output_dir / "stderr.log"
    if not stdout.exists():
        stdout.write_text(result.stdout or "", encoding="utf-8")
    if not stderr.exists():
        stderr.write_text(result.stderr or "", encoding="utf-8")
    metadata = output_dir / "adapter_result.json"
    if not metadata.exists():
        metadata.write_text(
            json.dumps(result.to_dict(include_output=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Convenience CLI alias; the option parser lives in ``orchestrator.py``."""

    from orchestrator import main as orchestrator_main

    return orchestrator_main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
