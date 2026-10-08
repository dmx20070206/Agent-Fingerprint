"""Complete-cycle smoke test using the bundled local Mock OpenAI service."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from agent_fingerprint.adapters.base_adapter import AgentResult, BaseAgentAdapter
from agent_fingerprint.collection.gateway.mock_openai import MockOpenAIGateway
from agent_fingerprint.collection.runner import PipelineRunner


class _MockHttpAgent(BaseAgentAdapter):
    adapter_name = "mock-http-agent"
    SCRIPT = r'''
import json, os, urllib.request
from pathlib import Path
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(os.environ["PAGE"], timeout=3) as response:
    assert response.status == 200
events_url = os.environ["PAGE"].rsplit("/", 1)[0] + "/__agent_fingerprint__/events"
payload = {"run_id": os.environ["RUN_ID"], "session_id": "mock", "events": [{"seq": 1, "type": "click", "epoch_ms": 1}]}
request = urllib.request.Request(events_url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
with opener.open(request, timeout=3):
    pass
request = urllib.request.Request(os.environ["GATEWAY"] + "/chat/completions", data=json.dumps({"model": "mock-alias", "messages": []}).encode(), headers={"Content-Type": "application/json"}, method="POST")
with opener.open(request, timeout=3) as response:
    answer = json.loads(response.read().decode())
assert answer["choices"][0]["message"]["content"] == "DONE"
answer["task_success"] = True
answer["task_status"] = "synthetic_complete"
Path(os.environ["AGENT_OUTPUT_DIR"], "result.json").write_text(json.dumps(answer), encoding="utf-8")
'''

    def __init__(self, gateway, run_id):
        super().__init__(conda_env=None, executable=sys.executable, timeout=5)
        self.gateway = gateway
        self.run_id = run_id

    def run_task(self, url, prompt, output_dir) -> AgentResult:
        self.validate_task(url, prompt)
        path = self.prepare_output_dir(output_dir)
        return self.execute(
            [sys.executable, "-c", self.SCRIPT],
            output_dir=path,
            env=self.merged_environment(
                {"PAGE": url, "GATEWAY": self.gateway.base_url, "RUN_ID": self.run_id, "AGENT_OUTPUT_DIR": str(path)}
            ),
        )


class MockGatewayPipelineTest(unittest.TestCase):
    def test_full_cycle_without_paid_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            holder = {}

            def make_gateway(run_dir):
                value = MockOpenAIGateway(run_dir / "mock_gateway", delay_seconds=0.001)
                holder["gateway"] = value
                return value

            def make_agent(model=None, gateway=None):
                return _MockHttpAgent(gateway, "mock-cycle")

            runner = PipelineRunner(
                output_root=root / "runs",
                gateway_factory=make_gateway,
                use_network_probe=False,
                adapter_factory={"mock": make_agent},
            )
            result = runner.run_once("/01-minimal.html", "click", agent_name="mock", run_id="mock-cycle")
            self.assertTrue(result.success, result.manifest)
            self.assertEqual(result.manifest["gateway"]["latency_event_count"], 1)
            self.assertFalse((result.output_dir / "mock_gateway").exists())
            self.assertFalse((result.output_dir / "ui_trace.json").exists())
            self.assertTrue(
                (result.output_dir / "fingerprints" / "l3_browser_dynamic.json").is_file()
            )
            self.assertEqual(holder["gateway"].events[0]["model"], "mock-alias")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
