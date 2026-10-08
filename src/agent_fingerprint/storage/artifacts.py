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
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agent_fingerprint.adapters.base_adapter import AgentResult
from agent_fingerprint.collection.completion import CompletionResult
from agent_fingerprint.collection.gateway import LiteLLMGateway
from agent_fingerprint.collection.probes.traffic_sniffer import CaptureResult
from agent_fingerprint.collection.sandbox import TraceStore

from agent_fingerprint.storage.schema import *
from agent_fingerprint.storage.metadata import task_metadata, collector_metadata

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
        "task": {"url": resolved_url, "requested_url": requested_url, "prompt": prompt, **task_metadata(requested_url, prompt)},
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


class ArtifactWriter:
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
        task_id: str | None = None,
        site: str | None = None,
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
            "task": {"url": url, "prompt": prompt, **task_metadata(requested_url or url, prompt, task_id=task_id, site=site)},
            "collector": collector_metadata(self.collector_version),
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


