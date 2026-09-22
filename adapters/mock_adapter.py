"""A deterministic child-process Agent used for local pipeline smoke tests.

It is intentionally not an intelligent Web Agent.  The child performs an HTTP
GET, emits one synthetic UI event, and (when configured) calls the local
OpenAI-compatible gateway.  Keeping it as a normal adapter makes the complete
CLI lifecycle testable on machines without Conda, Chrome, or model keys.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .base_adapter import AgentResult, BaseAgentAdapter


_RUNNER = r'''
import json, os, urllib.request
from pathlib import Path
from urllib.parse import urlsplit

proxy = os.environ.get("AGENT_PROXY_URL")
proxy_handler = urllib.request.ProxyHandler(
    {"http": proxy, "https": proxy} if proxy else {}
)
opener = urllib.request.build_opener(proxy_handler)
page = os.environ["AGENT_TASK_URL"]
with opener.open(page, timeout=5) as response:
    html = response.read()
    if response.status != 200:
        raise RuntimeError("sandbox returned HTTP %s" % response.status)

parts = urlsplit(page)
events_url = "%s://%s/__agent_fingerprint__/events" % (parts.scheme, parts.netloc)
events = {
    "run_id": os.environ.get("AGENT_RUN_ID"),
    "session_id": "mock-agent",
    "events": [{"seq": 1, "type": "click", "epoch_ms": 1, "target": {"tag": "button"},
                "static_fingerprint": {"user_agent": "agent-fingerprint-mock", "platform": "python",
                                       "language": "en-US", "languages": ["en-US"],
                                       "viewport": {"width": None, "height": None, "device_pixel_ratio": 1}}}],
}
request = urllib.request.Request(
    events_url,
    data=json.dumps(events).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with opener.open(request, timeout=5):
    pass

answer = {"content": "NO_GATEWAY"}
gateway = os.environ.get("AGENT_GATEWAY_URL")
if gateway:
    request = urllib.request.Request(
        gateway.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": os.environ.get("AGENT_MODEL", "mock-model"), "messages": [{"role": "user", "content": "redacted"}]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("AGENT_GATEWAY_KEY", "mock-key")},
        method="POST",
    )
    with opener.open(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    answer = payload["choices"][0]["message"]

output = Path(os.environ["AGENT_OUTPUT_DIR"])
result = {"page_bytes": len(html), "answer": answer, "task_success": True, "task_status": "synthetic_complete"}
output.joinpath("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
'''


class MockAgentAdapter(BaseAgentAdapter):
    """Run the deterministic smoke-test child in the current Python env."""

    adapter_name = "mock-agent"

    def __init__(
        self,
        *,
        gateway_url: str | None = None,
        gateway_key: str | None = None,
        model: str | None = None,
        run_id: str | None = None,
        executable: str = sys.executable,
        **kwargs,
    ) -> None:
        super().__init__(conda_env=None, executable=executable, **kwargs)
        self.gateway_url = gateway_url.rstrip("/") if gateway_url else None
        self.gateway_key = gateway_key
        self.model = model
        self.run_id = run_id
        self.proxy_url: str | None = None

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        path = self.prepare_output_dir(output_dir)
        updates = {
            "AGENT_TASK_URL": url,
            "AGENT_TASK_PROMPT": prompt,
            "AGENT_OUTPUT_DIR": str(path),
            "AGENT_RUN_ID": self.run_id or path.parent.name,
        }
        if self.gateway_url:
            updates["AGENT_GATEWAY_URL"] = self.gateway_url
            updates["AGENT_GATEWAY_KEY"] = self.gateway_key or "mock-key"
        if self.model:
            updates["AGENT_MODEL"] = self.model
        if self.proxy_url:
            updates["AGENT_PROXY_URL"] = self.proxy_url
        updates.update(self.proxy_environment(self.proxy_url))
        return self.execute(
            self.conda_command(["-c", _RUNNER]),
            output_dir=path,
            env=self.merged_environment(updates),
            redact_values=(self.gateway_key,) if self.gateway_key else (),
        )
