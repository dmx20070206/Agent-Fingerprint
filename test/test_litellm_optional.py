"""Optional live LiteLLM smoke test.

The normal test suite deliberately avoids starting LiteLLM because its proxy
extras are large and require Python 3.11+.  Set
``AGENT_FINGERPRINT_LITELLM_SMOKE=1`` in the orchestrator environment to run
this test against a local deterministic upstream; it never calls a paid
provider.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, ProxyHandler, build_opener

from gateway.litellm_gateway import GatewayConfig, LiteLLMGateway
from gateway.mock_openai import MockOpenAIGateway


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


_configured_executable = os.environ.get("AGENT_FINGERPRINT_LITELLM_EXECUTABLE")
_EXECUTABLE = (
    _configured_executable
    if _configured_executable and (Path(_configured_executable).is_file() or shutil.which(_configured_executable))
    else shutil.which("litellm")
)
_ENABLED = os.environ.get("AGENT_FINGERPRINT_LITELLM_SMOKE") == "1" and bool(_EXECUTABLE)


@unittest.skipUnless(
    _ENABLED,
    "set AGENT_FINGERPRINT_LITELLM_SMOKE=1 and install litellm[proxy] to run the live local smoke test",
)
class LiveLiteLLMTest(unittest.TestCase):
    def test_proxy_routes_to_bundled_mock_upstream(self) -> None:
        assert _EXECUTABLE is not None
        opener = build_opener(ProxyHandler({}))
        with tempfile.TemporaryDirectory(prefix="agent-fingerprint-litellm-test-") as directory:
            root = Path(directory)
            upstream = MockOpenAIGateway(
                root / "upstream", port=_free_port(), api_key="mock-upstream-key"
            ).start()
            master_key = "sk-test-local-master"
            config = GatewayConfig(
                root / "source.yaml",
                {
                    "model_list": [
                        {
                            "model_name": "smoke-alias",
                            "litellm_params": {
                                "model": "openai/mock-model",
                                "api_base": upstream.base_url,
                                "api_key": "os.environ/MOCK_UPSTREAM_KEY",
                            },
                        }
                    ]
                },
            )
            gateway = LiteLLMGateway(
                config,
                host="127.0.0.1",
                port=_free_port(),
                executable=_EXECUTABLE,
                startup_timeout=60,
                output_dir=root / "proxy",
            )
            try:
                gateway.start(
                    env={
                        "LITELLM_MASTER_KEY": master_key,
                        "MOCK_UPSTREAM_KEY": "mock-upstream-key",
                    }
                )
                request = Request(
                    gateway.base_url + "/chat/completions",
                    data=json.dumps(
                        {"model": "smoke-alias", "messages": [{"role": "user", "content": "hello"}]}
                    ).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + master_key,
                    },
                    method="POST",
                )
                with opener.open(request, timeout=30) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(body["choices"][0]["message"]["content"], "DONE")
                self.assertEqual(len(upstream.events), 1)
                self.assertGreaterEqual(len((root / "proxy" / "gateway_events.jsonl").read_text().splitlines()), 1)
            finally:
                gateway.stop()
                upstream.stop()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
