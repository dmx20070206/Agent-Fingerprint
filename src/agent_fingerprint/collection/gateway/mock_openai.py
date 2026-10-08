"""Deterministic, local OpenAI-compatible test endpoint.

This module is deliberately dependency-free.  It is not an LLM and must not
be used as a quality benchmark; its purpose is to exercise the HTTP boundary,
request timing, routing, and artifact lifecycle without an API key or network
access.  The endpoint implements the subset of ``/v1/chat/completions`` used
by the adapters and returns a deterministic assistant message.

Python API::

    from agent_fingerprint.collection.gateway.mock_openai import MockOpenAIGateway
    with MockOpenAIGateway("data/mock", delay_seconds=0.02) as gateway:
        # pass gateway.base_url to an OpenAI-compatible client
        ...

CLI::

    python -m gateway.mock_openai --port 4010 --output-dir data/mock
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


class MockOpenAIError(RuntimeError):
    """Raised when the local mock service cannot be started."""


class _MockHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], owner: "MockOpenAIGateway") -> None:
        self.owner = owner
        super().__init__(address, _MockRequestHandler)


class _MockRequestHandler(BaseHTTPRequestHandler):
    server: _MockHTTPServer

    def log_message(self, *_args: object) -> None:  # pragma: no cover - noisy stdlib hook
        return

    def _send_json(self, status: int, value: Mapping[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(self) -> bool:
        expected = self.server.owner.api_key
        if not expected:
            return True
        return self.headers.get("Authorization") == f"Bearer {expected}"

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        path = urlsplit(self.path).path
        if path in {"/health", "/health/liveliness", "/ready"}:
            self._send_json(200, {"status": "ok"})
            return
        if path == "/v1/models":
            self._send_json(200, {"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
            return
        if path == "/__mock__/events":
            self._send_json(200, {"events": self.server.owner.events})
            return
        self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        path = urlsplit(self.path).path
        if path not in {"/chat/completions", "/v1/chat/completions"}:
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})
            return
        if not self._authorized():
            self._send_json(401, {"error": {"message": "invalid api key", "type": "authentication_error"}})
            return
        started_ns = time.perf_counter_ns()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(max(0, length))
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
            return

        # 模拟响应延迟
        if self.server.owner.delay_seconds:
            time.sleep(self.server.owner.delay_seconds)
        ended_ns = time.perf_counter_ns()
        model = str(payload.get("model") or "mock-model")
        request_id = self.headers.get("X-Request-ID") or f"mock-{uuid.uuid4().hex}"
        messages = payload.get("messages")
        prompt_tokens = _rough_token_count(messages)
        completion_tokens = _rough_token_count(self.server.owner.response_text)
        # 记录 completion 事件
        event = {
            "event": "completion",
            "request_id": request_id,
            "model": model,
            "started_ns": started_ns,
            "ended_ns": ended_ns,
            "latency_ms": round((ended_ns - started_ns) / 1_000_000, 3),
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.server.owner.record_event(event)
        # 返回固定响应
        self._send_json(
            200,
            {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.server.owner.response_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": event["usage"],
            },
        )


# 估算 token 数量
def _rough_token_count(value: Any) -> int:
    """Return a stable, privacy-preserving token approximation for test logs."""

    if value is None:
        return 0
    if isinstance(value, str):
        return max(1, (len(value) + 3) // 4)
    if isinstance(value, (list, tuple)):
        return sum(_rough_token_count(item) for item in value)
    if isinstance(value, dict):
        return sum(_rough_token_count(item) for item in value.values())
    return _rough_token_count(str(value))


class MockOpenAIGateway:
    """Threaded local server with a LiteLLM-like adapter surface."""

    def __init__(
        self,
        output_dir: Path | str | None = None,  # 日志目录
        *,
        host: str = "127.0.0.1",  # 监听地址
        port: int = 0,  # 监听端口
        delay_seconds: float = 0.0,  # 响应延迟
        response_text: str = "DONE",  # 固定回复文本
        api_key: str | None = None,  # 可选认证密钥
    ) -> None:
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        if not response_text:
            raise ValueError("response_text must not be empty")
        self.output_dir = Path(output_dir or "data/mock-gateway").expanduser().resolve()
        self.host = host
        self.port = port
        self.delay_seconds = delay_seconds
        self.response_text = response_text
        self.api_key = api_key
        self.server: _MockHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.started = False
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._latency_log = self.output_dir / "gateway_events.jsonl"

    @property
    def base_url(self) -> str:
        port = self.server.server_address[1] if self.server else self.port
        return f"http://{self.host}:{port}/v1"

    @property
    def health_url(self) -> str:
        port = self.server.server_address[1] if self.server else self.port
        return f"http://{self.host}:{port}/health/liveliness"

    @property
    def auth_key(self) -> str:
        # 即使服务端不要求认证，也返回一个非空 Key，
        # 因为很多 OpenAI 兼容客户端要求 api_key 不能为空。
        return self.api_key or "mock-key"

    @property
    def running(self) -> bool:
        return bool(self.started and self.thread and self.thread.is_alive())

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events]

    def latency_log_path(self) -> Path:
        return self._latency_log

    def start(self) -> "MockOpenAIGateway":
        if self.running:
            raise MockOpenAIError("mock gateway is already running")
        # 创建输出目录
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # 创建空的 gateway_events.jsonl
        self._latency_log.touch(exist_ok=True)
        # 创建 HTTP Server
        self.server = _MockHTTPServer((self.host, self.port), self)
        # 启动后台线程
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, name="mock-openai", daemon=True)
        self.thread.start()
        self.started = True
        # 写入 mock_gateway.json
        self._write_metadata()
        return self

    def stop(self) -> None:
        if self.server is None:
            return
        # 停止接受新请求
        server = self.server
        server.shutdown()
        server.server_close()
        if self.thread:
            self.thread.join(timeout=5)
        self.started = False
        # 更新元数据
        self._write_metadata()
        self.server = None
        self.thread = None

    def record_event(self, event: Mapping[str, Any]) -> None:
        # 过滤 Prompt、消息内容、密钥等敏感字段
        safe = {
            str(key): value
            for key, value in event.items()
            if str(key).lower() not in {"prompt", "messages", "content", "api_key", "authorization"}
        }
        # 放入内存事件列表
        with self._lock:
            self._events.append(dict(safe))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # 追加写入 gateway_events.jsonl
        with self._latency_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False, default=str) + "\n")

    # 写入 mock_gateway.json
    def _write_metadata(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "kind": "mock-openai",
            "base_url": self.base_url,
            "health_url": self.health_url,
            "delay_seconds": self.delay_seconds,
            "running": self.running,
            "event_count": len(self._events),
        }
        (self.output_dir / "mock_gateway.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def __enter__(self) -> "MockOpenAIGateway":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic local OpenAI-compatible test service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4010)
    parser.add_argument("--output-dir", type=Path, default=Path("data/mock-gateway"))
    parser.add_argument("--delay-ms", type=float, default=0.0)
    parser.add_argument("--response-text", default="DONE")
    parser.add_argument("--api-key")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        gateway = MockOpenAIGateway(
            args.output_dir,
            host=args.host,
            port=args.port,
            delay_seconds=args.delay_ms / 1000,
            response_text=args.response_text,
            api_key=args.api_key,
        ).start()
    except (MockOpenAIError, OSError, ValueError) as exc:
        print(f"mock_openai: {exc}")
        return 2
    print(f"Mock OpenAI endpoint ready at {gateway.base_url}", flush=True)
    try:
        while gateway.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        gateway.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
