"""Tests for the dependency-free deterministic LLM endpoint."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from gateway.mock_openai import MockOpenAIGateway


class MockOpenAITest(unittest.TestCase):
    def test_chat_completion_and_latency_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gateway = MockOpenAIGateway(Path(directory), delay_seconds=0.001).start()
            try:
                opener = build_opener(ProxyHandler({}))
                body = json.dumps({"model": "alias", "messages": [{"role": "user", "content": "private"}]}).encode()
                request = Request(
                    gateway.base_url + "/chat/completions",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with opener.open(request, timeout=3) as response:
                    payload = json.loads(response.read().decode())
                self.assertEqual(payload["choices"][0]["message"]["content"], "DONE")
                self.assertEqual(gateway.events[0]["model"], "alias")
                self.assertGreaterEqual(gateway.events[0]["latency_ms"], 0)
            finally:
                gateway.stop()
            log = Path(directory) / "gateway_events.jsonl"
            self.assertTrue(log.is_file())
            self.assertNotIn("private", log.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

