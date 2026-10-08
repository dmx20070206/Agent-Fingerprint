"""Public CLI smoke test: sandbox + mock gateway + real child + manifest."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliEndToEndTest(unittest.TestCase):
    def test_mock_cli_cycle_needs_no_external_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_file = Path(directory) / "tasks.json"
            task_file.write_text(json.dumps([{"url": "/01-minimal.html", "prompt": "smoke"}]))
            environment = os.environ.copy()
            environment.pop("HTTP_PROXY", None)
            environment.pop("HTTPS_PROXY", None)
            environment.pop("ALL_PROXY", None)
            source_path = str(PROJECT_ROOT / "src")
            environment["PYTHONPATH"] = os.pathsep.join(
                part for part in (source_path, environment.get("PYTHONPATH", "")) if part
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent_fingerprint",
                    "collect",
                    "--agent",
                    "mock",
                    "--mock-llm",
                    "--no-network-probe",
                    "--task-file",
                    str(task_file),
                    "--run-id",
                    "cli-e2e",
                    "--output-root",
                    directory,
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            summary = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(summary["status"], "success")
            run_dir = Path(summary["output_dir"])
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["gateway"]["latency_event_count"], 1)
            self.assertEqual(
                json.loads(
                    (run_dir / "fingerprints" / "l3_browser_dynamic.json").read_text()
                )["event_count"],
                1,
            )
            self.assertFalse((run_dir / "logs" / "mock_gateway").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
