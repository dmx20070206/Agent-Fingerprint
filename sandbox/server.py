"""Small local HTTP server for the Web Agent sandbox.

The server deliberately uses only the Python standard library so that it can be
started before installing any agent framework.  It is also exposed as a couple
of functions, which makes it straightforward to start it from an orchestrator
or from a unit test without spawning a shell command.

Examples
--------
Start the default pages on an ephemeral port::

    python -m sandbox.server --port 0

Use :func:`create_server` when the caller needs the bound port (for example,
``server.server_address[1]``) or wants to run the server in a background
thread.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import mimetypes
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

SANDBOX_ROOT = Path(__file__).resolve().parent
STATIC_DIR = SANDBOX_ROOT / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_INTERACTION_DELAY_SECONDS = 0
MONITOR_ENDPOINT = "/__agent_fingerprint__/events"
MONITOR_SCRIPT_ENDPOINT = "/__agent_fingerprint__/monitor.js"
SITE_POST_ENDPOINTS = {
    "/complete",
    "/complete_update",
    "/end",
    "/fingerprint",
    "/fp",
    "/mm",
    "/mouse_movement",
    "/start",
}


class TraceStore:
    """Thread-safe in-memory store for browser UI events.

    Pages send batches through :data:`MONITOR_ENDPOINT`.  Events are keyed by
    ``run_id`` and ``session_id`` so navigation (which reloads the probe) and
    periodic retransmission do not create duplicates.
    """

    def __init__(self) -> None:
        self._events: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._reliable_sessions: dict[str, set[str]] = {}
        self._finalize_requested: set[str] = set()
        self._finalized: dict[str, dict[str, int]] = {}

    @staticmethod
    def _contiguous_seq(events: dict[int, dict[str, Any]]) -> int:
        """Return the largest sequence for which every event from 1 exists."""

        acknowledged = 0
        while acknowledged + 1 in events:
            acknowledged += 1
        return acknowledged

    def receive(self, payload: dict[str, Any], default_run_id: str = "default") -> dict[str, Any]:
        """Store one upload batch and return an explicit sequence ACK.

        Protocol-v2 clients use ``ack_seq`` as their upload cursor. Replayed
        batches are harmless because events are de-duplicated by session and
        sequence number.
        """

        run_id = str(payload.get("run_id") or default_run_id)
        session_id = str(payload.get("session_id") or "unknown")
        incoming = payload.get("events") or []
        if not isinstance(incoming, list):
            incoming = []
        try:
            protocol_version = int(payload.get("protocol_version") or 1)
        except (TypeError, ValueError):
            protocol_version = 1

        with self._condition:
            sessions = self._events.setdefault(run_id, {})
            events = sessions.setdefault(session_id, {})
            if protocol_version >= 2:
                self._reliable_sessions.setdefault(run_id, set()).add(session_id)
            added = 0
            for item in incoming:
                if not isinstance(item, dict):
                    continue
                seq = item.get("seq")
                try:
                    seq = int(seq)
                except (TypeError, ValueError):
                    seq = len(events) + 1
                if seq < 1:
                    continue
                if seq not in events:
                    events[seq] = dict(item)
                    added += 1

            ack_seq = self._contiguous_seq(events)
            final_seq = payload.get("final_seq")
            try:
                final_seq = int(final_seq) if final_seq is not None else None
            except (TypeError, ValueError):
                final_seq = None
            if payload.get("final") is True and final_seq is not None and final_seq >= 0 and ack_seq >= final_seq:
                finalized = self._finalized.setdefault(run_id, {})
                finalized[session_id] = max(finalized.get(session_id, 0), final_seq)

            self._condition.notify_all()
            return {
                "ok": True,
                "added": added,
                "ack_seq": ack_seq,
                "finalize_requested": run_id in self._finalize_requested,
                "finalized": session_id in self._finalized.get(run_id, {}),
            }

    def append(self, payload: dict[str, Any], default_run_id: str = "default") -> int:
        """Compatibility wrapper for callers that only need the added count."""

        return int(self.receive(payload, default_run_id)["added"])

    def control(self, run_id: str, session_id: str, protocol_version: int = 2) -> dict[str, Any]:
        """Return collector control state for a browser session."""

        run_id = str(run_id)
        session_id = str(session_id)
        with self._condition:
            if protocol_version >= 2:
                self._reliable_sessions.setdefault(run_id, set()).add(session_id)
            events = self._events.get(run_id, {}).get(session_id, {})
            return {
                "ok": True,
                "ack_seq": self._contiguous_seq(events),
                "finalize_requested": run_id in self._finalize_requested,
                "finalized": session_id in self._finalized.get(run_id, {}),
            }

    def request_finalize(self, run_id: str) -> set[str]:
        """Ask active protocol-v2 pages to stop and upload their final event."""

        run_id = str(run_id)
        with self._condition:
            self._finalize_requested.add(run_id)
            expected = set(self._reliable_sessions.get(run_id, set()))
            self._condition.notify_all()
            return expected

    def wait_for_finalization(
        self,
        run_id: str,
        sessions: set[str],
        timeout: float,
    ) -> dict[str, Any]:
        """Wait until all named sessions have acknowledged their final seq."""

        run_id = str(run_id)
        expected = set(sessions)
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                complete = set(self._finalized.get(run_id, {}))
                missing = expected - complete
                if not missing:
                    return {"complete": True, "expected": sorted(expected), "missing": []}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"complete": False, "expected": sorted(expected), "missing": sorted(missing)}
                self._condition.wait(remaining)

    def export(self, run_id: str = "default") -> dict[str, Any]:
        with self._lock:
            sessions = self._events.get(run_id, {})
            events = []
            for session_id, by_seq in sessions.items():
                for item in by_seq.values():
                    event = dict(item)
                    event.setdefault("session_id", session_id)
                    events.append(event)
        # A malformed client event must not take down the whole collection
        # cycle.  Browser timestamps are numeric in normal operation, but
        # sort defensively when a hand-written/custom probe sends null/text.
        events.sort(key=lambda item: (_numeric_sort_value(item.get("epoch_ms")), _numeric_sort_value(item.get("seq"))))
        return {
            "schema": "agent-fingerprint-ui-trace/v1",
            "run_id": run_id,
            "event_count": len(events),
            "events": events,
        }

    def clear(self, run_id: str | None = None) -> None:
        with self._condition:
            if run_id is None:
                self._events.clear()
                self._reliable_sessions.clear()
                self._finalize_requested.clear()
                self._finalized.clear()
            else:
                self._events.pop(run_id, None)
                self._reliable_sessions.pop(run_id, None)
                self._finalize_requested.discard(run_id)
                self._finalized.pop(run_id, None)
            self._condition.notify_all()


def _numeric_sort_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTP server that can handle independent browser requests concurrently."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        handler_class,
        *,
        trace_store=None,
        inject_monitor=False,
        run_id="default",
        interaction_delay_ms=0,
    ):
        self.trace_store = trace_store or TraceStore()
        self.inject_monitor = bool(inject_monitor)
        self.run_id = run_id
        self.interaction_delay_ms = int(interaction_delay_ms)
        super().__init__(server_address, handler_class)


class SandboxRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static-file handler with optional monitor injection and trace intake."""

    # Resolve the probe from the repository rather than copying it into static.
    monitor_path = Path(__file__).resolve().parents[1] / "probes" / "inject_monitor.js"

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.path.startswith(MONITOR_ENDPOINT):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json_response(self, value: Any, status: int = 200) -> None:
        self._send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json", status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == MONITOR_SCRIPT_ENDPOINT:
            try:
                body = self.monitor_path.read_bytes()
            except OSError:
                self._json_response({"error": "monitor script unavailable"}, 404)
                return
            self._send_bytes(body, "application/javascript; charset=utf-8")
            return
        if parsed.path == MONITOR_ENDPOINT:
            query = urllib.parse.parse_qs(parsed.query)
            run_id = query.get("run_id", [self.server.run_id])[0]
            if query.get("control", [""])[0] == "1":
                session_id = query.get("session_id", ["unknown"])[0]
                try:
                    protocol_version = int(query.get("protocol_version", ["2"])[0])
                except (TypeError, ValueError):
                    protocol_version = 2
                self._json_response(self.server.trace_store.control(run_id, session_id, protocol_version))
                return
            self._json_response(self.server.trace_store.export(run_id))
            return
        if getattr(self.server, "inject_monitor", False) and parsed.path.lower().endswith((".html", ".htm")):
            self._serve_html_with_monitor(parsed.path)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in SITE_POST_ENDPOINTS:
            # The upstream deployment persisted these events to PostgreSQL.
            # Locally, the injected project monitor is the source of truth.
            length = int(self.headers.get("Content-Length", "0"))
            if length > 0:
                self.rfile.read(length)
            self._json_response({"ok": True})
            return
        if parsed.path != MONITOR_ENDPOINT:
            self.send_error(404, "Unknown probe endpoint")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(max(0, length)).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            acknowledgement = self.server.trace_store.receive(payload, self.server.run_id)
            self._json_response(acknowledgement)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json_response({"ok": False, "error": str(exc)}, 400)

    def do_OPTIONS(self) -> None:  # noqa: N802 - allow cross-origin probe ACKs
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != MONITOR_ENDPOINT:
            self.send_error(404, "Unknown probe endpoint")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_html_with_monitor(self, request_path: str) -> None:
        # Use SimpleHTTPRequestHandler's safe path normalization, then read and
        # transform only the HTML response in memory.
        local_path = Path(self.translate_path(request_path))
        if not local_path.is_file():
            self.send_error(404, "File not found")
            return
        self._serve_html_file_with_monitor(local_path)

    def _serve_html_file_with_monitor(self, local_path: Path) -> None:
        try:
            html = local_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            super().do_GET()
            return
        query = urllib.parse.urlencode({"run_id": self.server.run_id})
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        monitor_endpoint = f"http://{host}{MONITOR_ENDPOINT}"
        bootstrap = (
            "<script>window.__AGENT_FINGERPRINT_CONFIG__="
            + json.dumps(
                {
                    "endpoint": monitor_endpoint,
                    "run_id": self.server.run_id,
                    "interaction_delay_ms": self.server.interaction_delay_ms,
                },
                ensure_ascii=False,
            )
            + ";</script>"
            + f'<script src="{MONITOR_SCRIPT_ENDPOINT}?{query}"></script>'
        )
        lower = html.lower()
        head_start = lower.find("<head")
        head_open_end = html.find(">", head_start) if head_start >= 0 else -1
        if head_open_end >= 0:
            # Install the monitor before page-owned head scripts. This makes
            # the interaction gate effective from the earliest scriptable
            # point in the document lifecycle.
            html = html[: head_open_end + 1] + bootstrap + html[head_open_end + 1 :]
        else:
            # A doctype only selects standards mode when it is the first HTML
            # token.  Many compact fixtures omit explicit <html>/<head> tags;
            # prepending a script would silently put those pages into quirks
            # mode and change the layout we are trying to measure.
            doctype_end = html.find(">") if lower.lstrip().startswith("<!doctype") else -1
            leading = len(html) - len(html.lstrip())
            if doctype_end >= leading:
                html = html[: doctype_end + 1] + bootstrap + html[doctype_end + 1 :]
            else:
                html = bootstrap + html
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")


def create_handler(directory: Path | str = STATIC_DIR) -> type[http.server.SimpleHTTPRequestHandler]:
    """Return a request handler rooted at *directory*.

    ``SimpleHTTPRequestHandler`` already normalizes URL paths and prevents
    traversal outside its directory.  Supplying ``directory`` explicitly keeps
    the process working-directory independent, which is important when the
    orchestrator starts this module from another directory.
    """

    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Sandbox static directory does not exist: {root}")

    return functools.partial(SandboxRequestHandler, directory=str(root))


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    directory: Path | str = STATIC_DIR,
    *,
    trace_store: TraceStore | None = None,
    inject_monitor: bool = False,
    run_id: str = "default",
    interaction_delay_seconds: float = DEFAULT_INTERACTION_DELAY_SECONDS,
) -> ThreadingHTTPServer:
    """Create (but do not start) a sandbox server.

    Passing ``port=0`` asks the OS for a free port, which is recommended for
    tests and for running multiple sandbox instances in parallel.
    """

    if interaction_delay_seconds < 0:
        raise ValueError("interaction_delay_seconds cannot be negative")
    handler = create_handler(directory)
    return ThreadingHTTPServer(
        (host, port),
        handler,
        trace_store=trace_store,
        inject_monitor=inject_monitor,
        run_id=run_id,
        interaction_delay_ms=round(interaction_delay_seconds * 1000),
    )


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    directory: Path | str = STATIC_DIR,
    *,
    inject_monitor: bool = False,
    interaction_delay_seconds: float = DEFAULT_INTERACTION_DELAY_SECONDS,
) -> None:
    """Run a blocking server until interrupted."""

    server = create_server(
        host=host,
        port=port,
        directory=directory,
        inject_monitor=inject_monitor,
        interaction_delay_seconds=interaction_delay_seconds,
    )
    address, bound_port = server.server_address
    print(f"Sandbox serving {Path(directory).resolve()} at http://{address}:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve local Web Agent sandbox pages")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port; use 0 for a free port")
    parser.add_argument(
        "--directory",
        type=Path,
        default=STATIC_DIR,
        help="Directory containing static pages (default: %(default)s)",
    )
    parser.add_argument(
        "--inject-monitor",
        action="store_true",
        help="Inject inject_monitor.js into served HTML and collect UI events",
    )
    parser.add_argument(
        "--interaction-delay",
        type=float,
        default=DEFAULT_INTERACTION_DELAY_SECONDS,
        help="Seconds to block page interaction after the monitor loads (default: %(default)s)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_parser().parse_args(argv)
    serve(
        host=args.host,
        port=args.port,
        directory=args.directory,
        inject_monitor=args.inject_monitor,
        interaction_delay_seconds=args.interaction_delay,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through CLI manually
    main()
