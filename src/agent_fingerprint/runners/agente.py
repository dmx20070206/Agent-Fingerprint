
import json
import os
import urllib.request
from pathlib import Path

endpoint = os.environ["AGENTE_ENDPOINT"].rstrip("/")
payload_data = {"command": os.environ["AGENTE_COMMAND"], "clientid": os.environ.get("AGENTE_RUN_ID")}
max_steps = os.environ.get("AGENTE_MAX_STEPS")
if max_steps:
    payload_data["planner_max_chat_round"] = int(max_steps)
model = os.environ.get("AGENTE_LLM_MODEL")
api_base = os.environ.get("AGENTE_LLM_API_BASE")
if model and api_base:
    model_config = {
        "model_name": model,
        "model_api_key": os.environ.get("AGENTE_LLM_API_KEY") or "sk-placeholder",
        "model_base_url": api_base,
        "llm_config_params": {"cache_seed": None, "temperature": 0.1, "top_p": 0.1},
    }
    payload_data["llm_config"] = {
        "planner_agent": dict(model_config),
        "browser_nav_agent": dict(model_config),
    }
payload = json.dumps(payload_data).encode("utf-8")
request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(request, timeout=float(os.environ.get("AGENTE_HTTP_TIMEOUT", "1800"))) as response:
    body = response.read()
output = Path(os.environ["AGENT_OUTPUT_DIR"])
output.joinpath("response.txt").write_bytes(body)
body_text = body.decode("utf-8", errors="replace")
notifications = []
for line in body_text.splitlines():
    if not line.startswith("data:"):
        continue
    try:
        value = json.loads(line[5:].strip())
    except (TypeError, ValueError):
        continue
    if isinstance(value, dict):
        notifications.append(value)
terminal_type = next(
    (item.get("type") for item in reversed(notifications)
     if item.get("type") in {"transaction_done", "max_turns_reached", "error"}),
    None,
)
task_success = True if terminal_type == "transaction_done" else False if terminal_type else None
task_status = {
    "transaction_done": "completed",
    "max_turns_reached": "max_turns_reached",
    "error": "error",
}.get(terminal_type, "incomplete")
result = {
    "endpoint": endpoint,
    "bytes": len(body),
    "event_count": len(notifications),
    "task_success": task_success,
    "task_status": task_status,
}
output.joinpath("response.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
output.joinpath("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(body_text)
