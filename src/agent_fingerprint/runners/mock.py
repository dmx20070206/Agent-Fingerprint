
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
