"""End-to-end data-collection cycle for the Agent-Fingerprint project.

``PipelineRunner`` owns the lifecycle described in the project design:

    sandbox HTTP server -> LiteLLM gateway -> network probe -> Agent adapter
    -> artifact collection -> reverse-order cleanup

The default tests use fake adapters/tools, so this orchestration layer can be
verified without a paid LLM API or browser installation.  Real adapters can be
passed through ``adapter_factory`` in exactly the same way.
"""

from __future__ import annotations

from agent_fingerprint.paths import PROJECT_ROOT

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

from agent_fingerprint.adapters import (
    AgentEAdapter,
    AutoGenAdapter,
    BrowserUseAdapter,
    ManualAdapter,
    MockAgentAdapter,
    SkyvernAdapter,
    WebVoyagerAdapter,
)
from agent_fingerprint.adapters.base_adapter import AgentInterruptedError, AgentResult
from agent_fingerprint.collection.completion import CompletionResult, VERIFIED_AGENTS
from agent_fingerprint.collection.gateway import GatewayConfig, LiteLLMGateway
from agent_fingerprint.collection.probes.traffic_sniffer import CaptureResult, CaptureSession, TrafficSniffer
from agent_fingerprint.collection.sandbox import TraceStore, create_server

from agent_fingerprint.storage.artifacts import (
    ArtifactWriter, _archive_nonempty_log, _archive_numbered_files, _artifact_name, _canonical_run_files, _clear_managed_run_artifacts, _compact_agent_result, _count_jsonl, _ensure_agent_logs, _fallback_manifest, _organize_run_files, _read_json, _read_jsonl_files, _read_jsonl_path, _safe_path_attr, _safe_path_call, _unique_destination
)
from agent_fingerprint.collection.factories import ResourceFactories, _is_running, _call_factory, _gateway_key, _infer_provider_key_env
from agent_fingerprint.storage.run_store import _append_run_index

__all__ = ["CycleResult", "PipelineRunner"]


from agent_fingerprint.storage.schema import *

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


class PipelineRunner(ResourceFactories, ArtifactWriter):
    """Run one or many isolated collection cycles."""

    def __init__(
        self,
        *,
        output_root: Path | str = "data/runs",
        sandbox_directory: Path | str | None = None,
        start_sandbox: bool = True,
        inject_monitor: bool = True,
        collector_version: str = "legacy/v2",
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
        if collector_version not in {"legacy/v2", "semantic/v1"}:
            raise ValueError("Unsupported collector version")
        self.collector_version = collector_version
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
            directory=self.sandbox_directory or PROJECT_ROOT / "sandbox" / "static",
            trace_store=trace_store,
            collector_version=self.collector_version,
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
        task_id: str | None = None,
        site: str | None = None,
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
                task_id=task_id,
                site=site,
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
                            task_id=task.get("task_id"),
                            site=task.get("site"),
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








































def main(argv: Sequence[str] | None = None) -> int:
    """Convenience CLI entry point for running collection cycles."""

    from agent_fingerprint.cli.collect import main as orchestrator_main

    return orchestrator_main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
