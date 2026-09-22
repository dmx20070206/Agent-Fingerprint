"""Capture network traffic for one Agent run.

The module deliberately starts external tools without ``shell=True``.  It can
use either:

* ``tcpdump`` for packet-level PCAP capture (usually requires CAP_NET_RAW or
  root privileges); or
* ``mitmdump`` for HTTP/HTTPS proxy-flow capture (the browser must be pointed at
  the printed proxy address, or the caller must configure its own proxy).

Python API::

    from probes.traffic_sniffer import TrafficSniffer
    with TrafficSniffer("./data/task", backend="tcpdump") as capture:
        run_agent()
    print(capture.result.output_path)

CLI::

    python probes/traffic_sniffer.py --backend tcpdump --output-dir data/task \
        --interface any --duration 60
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

BACKENDS = {"tcpdump"}


class SnifferError(RuntimeError):
    """Raised when a capture cannot be started or stopped safely."""


@dataclass(slots=True)
class CaptureResult:
    backend: str
    command: tuple[str, ...]
    output_path: Path
    output_dir: Path
    started_at: str
    stopped_at: str | None
    returncode: int | None
    duration_seconds: float | None
    stdout_log: Path
    stderr_log: Path
    error: str | None = None

    @property
    def success(self) -> bool:
        # tcpdump commonly exits with 130 after the intentional SIGINT used to
        # flush and close a capture.  Treat that normal shutdown as success.
        return self.returncode == 0 or (self.backend == "tcpdump" and self.returncode in {130, -signal.SIGINT})

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["success"] = self.success
        data["command"] = list(self.command)
        for key in ("output_path", "output_dir", "stdout_log", "stderr_log"):
            data[key] = str(data[key])
        return data


class CaptureSession:
    """A running capture process returned by :meth:`TrafficSniffer.start`."""

    def __init__(
        self,
        sniffer: "TrafficSniffer",
        process: subprocess.Popen[str],
        result: CaptureResult,
        streams: tuple[object, object],
    ):
        self.sniffer = sniffer
        self.process = process
        self.result = result
        self._streams = streams
        self._stopped = False

    def stop(self, timeout: float | None = None) -> CaptureResult:
        if self._stopped:
            return self.result
        timeout = self.sniffer.stop_timeout if timeout is None else timeout
        error = None
        try:
            _interrupt_process(self.process)
            try:
                returncode = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    returncode = self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    returncode = self.process.wait(timeout=2)
        except OSError as exc:
            returncode = getattr(self.process, "returncode", None)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            for stream in self._streams:
                try:
                    stream.close()
                except Exception:
                    pass

        stopped = datetime.now(timezone.utc).isoformat()
        self.result = CaptureResult(
            backend=self.result.backend,
            command=self.result.command,
            output_path=self.result.output_path,
            output_dir=self.result.output_dir,
            started_at=self.result.started_at,
            stopped_at=stopped,
            returncode=returncode,
            duration_seconds=max(0.0, time.monotonic() - self.sniffer._started_monotonic),
            stdout_log=self.result.stdout_log,
            stderr_log=self.result.stderr_log,
            error=error,
        )
        self.sniffer._write_metadata(self.result)
        self._stopped = True
        return self.result

    def __enter__(self) -> "CaptureSession":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()


class TrafficSniffer:
    """Build and manage one tcpdump or mitmdump process."""

    def __init__(
        self,
        output_dir: Path | str,
        *,
        backend: str = "tcpdump",
        interface: str | None = "any",
        capture_filter: str | Sequence[str] | None = None,
        executable: str | None = None,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8080,
        snaplen: int = 0,
        startup_timeout: float = 10,
        stop_timeout: float = 10,
    ) -> None:
        if listen_port < 1 or listen_port > 65535:
            raise ValueError("listen_port must be between 1 and 65535")
        if snaplen < 0:
            raise ValueError("snaplen cannot be negative")
        if stop_timeout <= 0:
            raise ValueError("stop_timeout must be positive")
        if startup_timeout <= 0:
            raise ValueError("startup_timeout must be positive")
        self.output_dir = Path(output_dir).expanduser().resolve()
        if backend not in BACKENDS:
            raise ValueError(f"unsupported backend: {backend}")
        self.backend = backend
        self.interface = interface
        self.capture_filter = capture_filter
        self.executable = executable or "tcpdump"
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.snaplen = snaplen
        self.startup_timeout = startup_timeout
        self.stop_timeout = stop_timeout
        self._session: CaptureSession | None = None
        self._started_monotonic = 0.0

    @property
    def output_path(self) -> Path:
        return self.output_dir / ("traffic.pcap" if self.backend == "tcpdump" else "traffic.mitm")

    def command(self) -> list[str]:
        if self.backend == "tcpdump":
            command = [self.executable, "-U", "-n", "-w", str(self.output_path)]
            if self.interface:
                command.extend(["-i", self.interface])
            if self.snaplen:
                command.extend(["-s", str(self.snaplen)])
            command.extend(_filter_args(self.capture_filter))
            return command
        command = [
            self.executable,
            "--listen-host",
            self.listen_host,
            "--listen-port",
            str(self.listen_port),
            "-w",
            str(self.output_path),
        ]
        # mitmdump does not use tcpdump's BPF positional filter syntax.  Keep
        # the option available for future mitmproxy addons, but do not append
        # a misleading token sequence to the command.
        return command

    def start(self) -> CaptureSession:
        if self._session and not self._session._stopped:
            raise SnifferError("capture is already running")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.backend == "mitmproxy":
            # A stale process on the configured port can make a plain TCP
            # readiness probe return success even while the new mitmdump child
            # failed to bind.  Reject that case before spawning the child.
            self._assert_listen_port_available()
        command = tuple(self.command())
        started_at = datetime.now(timezone.utc).isoformat()
        stdout_path = self.output_dir / "sniffer.stdout.log"
        stderr_path = self.output_dir / "sniffer.stderr.log"
        stdout_file = stdout_path.open("w", encoding="utf-8")
        stderr_file = stderr_path.open("w", encoding="utf-8")
        self._started_monotonic = time.monotonic()
        try:
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=(os.name != "nt"),
                text=True,
            )
        except (OSError, ValueError) as exc:
            stdout_file.close()
            stderr_file.close()
            raise SnifferError(f"unable to start {self.backend}: {exc}") from exc
        # Catch permission errors and malformed command lines immediately.  A
        # capture process that exits before the Agent starts would otherwise
        # produce a misleadingly complete run with no PCAP.  mitmdump also
        # needs to be listening before the Agent gets its proxy URL; merely
        # observing a live PID leaves a small but real connection-refused race.
        try:
            if self.backend == "mitmproxy":
                self._wait_for_listener(process, stderr_path)
            else:
                time.sleep(0.05)
                if process.poll() is not None:
                    raise SnifferError(
                        f"{self.backend} exited immediately with code {process.returncode}; " f"see {stderr_path}"
                    )
        except BaseException:
            _stop_started_process(process, self.stop_timeout)
            for stream in (stdout_file, stderr_file):
                try:
                    stream.flush()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            raise
        result = CaptureResult(
            backend=self.backend,
            command=command,
            output_path=self.output_path,
            output_dir=self.output_dir,
            started_at=started_at,
            stopped_at=None,
            returncode=None,
            duration_seconds=None,
            stdout_log=stdout_path,
            stderr_log=stderr_path,
        )
        session = CaptureSession(self, process, result, (stdout_file, stderr_file))
        self._session = session
        try:
            self._write_metadata(result)
        except Exception as exc:
            # Metadata is part of the contract, but a read-only/full output
            # filesystem must not leave the capture child running after start
            # reports an error.  ``stop`` also closes both log streams.
            try:
                session.stop()
            except Exception:
                pass
            self._session = None
            raise SnifferError(f"unable to write capture metadata: {exc}") from exc
        return session

    def _wait_for_listener(self, process: subprocess.Popen[str], stderr_path: Path) -> None:
        """Wait until mitmdump accepts a TCP connection on its listen port."""

        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None
        host = self.listen_host
        # Wildcard bind addresses are not valid connection destinations.
        if host in {"0.0.0.0", ""}:
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise SnifferError(f"mitmproxy exited during startup with code {process.returncode}; see {stderr_path}")
            try:
                with socket.create_connection((host, self.listen_port), timeout=0.2):
                    # The listener may be a stale process or the child may
                    # exit immediately after accepting the probe connection.
                    if process.poll() is None:
                        return
                    raise SnifferError(
                        f"mitmproxy exited during startup with code {process.returncode}; see {stderr_path}"
                    )
            except (OSError, TimeoutError) as exc:
                last_error = exc
            time.sleep(0.05)
        raise SnifferError(
            f"mitmproxy did not listen on {self.listen_host}:{self.listen_port} "
            f"within {self.startup_timeout}s ({last_error})"
        )

    def _assert_listen_port_available(self) -> None:
        """Fail before spawning if the mitmproxy listen address is occupied."""

        try:
            addresses = socket.getaddrinfo(
                self.listen_host,
                self.listen_port,
                type=socket.SOCK_STREAM,
                flags=socket.AI_PASSIVE,
            )
        except OSError as exc:
            raise SnifferError(f"unable to resolve mitmproxy listen host {self.listen_host!r}: {exc}") from exc
        seen: set[tuple[int, tuple[object, ...]]] = set()
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
                raise SnifferError(
                    f"mitmproxy address {self.listen_host}:{self.listen_port} is already in use or unavailable: {exc}"
                ) from exc
            finally:
                sock.close()

    @property
    def proxy_url(self) -> str | None:
        """Return the proxy URL for mitmproxy captures."""

        if self.backend != "mitmproxy":
            return None
        return f"http://{self.listen_host}:{self.listen_port}"

    def stop(self, timeout: float | None = None) -> CaptureResult:
        if not self._session:
            raise SnifferError("capture has not been started")
        return self._session.stop(timeout)

    def __enter__(self) -> CaptureSession:
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()

    def _write_metadata(self, result: CaptureResult) -> None:
        metadata_path = self.output_dir / "traffic_capture.json"
        metadata_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _filter_args(capture_filter: str | Sequence[str] | None) -> list[str]:
    if capture_filter is None:
        return []
    if isinstance(capture_filter, str):
        return shlex.split(capture_filter)
    return [str(item) for item in capture_filter]


def _interrupt_process(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        if process.poll() is not None:
            return
        process.send_signal(signal.CTRL_BREAK_EVENT)
        return
    # A process-group leader can exit while a descendant (for example a
    # mitmproxy worker) still owns the listen socket.  Send to the dedicated
    # process group even when ``poll()`` says the leader is gone.
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        return


def _stop_started_process(process: subprocess.Popen[str], timeout: float) -> None:
    """Best-effort cleanup for a child that failed during ``start``."""

    try:
        _interrupt_process(process)
        if process.poll() is None:
            process.wait(timeout=timeout)
        else:
            # Reap an already-exited leader; descendants, if any, were still
            # addressed through the process-group signal above.
            process.wait(timeout=0)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture Agent network traffic")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="tcpdump")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--interface", help="tcpdump interface, e.g. any or eth0")
    parser.add_argument("--filter", dest="capture_filter", help="tcpdump/mitmproxy filter expression")
    parser.add_argument("--executable", help="Override tcpdump or mitmdump executable")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8080)
    parser.add_argument("--snaplen", type=int, default=0)
    parser.add_argument("--startup-timeout", type=float, default=10)
    parser.add_argument("--duration", type=float, help="Stop automatically after this many seconds")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    sniffer = TrafficSniffer(
        args.output_dir,
        backend=args.backend,
        interface=args.interface,
        capture_filter=args.capture_filter,
        executable=args.executable,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        snaplen=args.snaplen,
        startup_timeout=args.startup_timeout,
    )
    try:
        session = sniffer.start()
        print(json.dumps({"pid": session.process.pid, "command": list(session.result.command)}, ensure_ascii=False))
        if args.duration is not None:
            if args.duration <= 0:
                raise ValueError("duration must be positive")
            time.sleep(args.duration)
        else:
            print("Capture is running; press Ctrl-C to stop.")
            while session.process.poll() is None:
                time.sleep(0.5)
        result = session.stop()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.success else 1
    except KeyboardInterrupt:
        if "session" in locals():
            session.stop()
        return 130
    except (SnifferError, ValueError) as exc:
        if "session" in locals() and not session._stopped:
            session.stop()
        print(f"traffic_sniffer: {exc}", flush=True)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
