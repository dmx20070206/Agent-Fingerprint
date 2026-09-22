"""Tests for the opt-in v1 to v2 run-layout migration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from analysis._common import resolve_run_dir
from scripts.migrate_runs_v1_to_v2 import migrate


class RunMigrationTest(unittest.TestCase):
    def test_dry_run_is_non_mutating_and_apply_preserves_media_and_pcap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "skyvern_example"
            agent = source / "logs" / "agent"
            fingerprints = source / "fingerprints"
            recordings = agent / "skyvern_artifacts" / "recordings"
            screenshots = agent / "skyvern_artifacts" / "screenshots"
            for path in (agent, fingerprints, recordings, screenshots):
                path.mkdir(parents=True, exist_ok=True)
            manifest = {
                "schema": "agent-fingerprint-run/v1",
                "run_id": "skyvern_example",
                "started_at": "2026-09-19T12:00:00+00:00",
                "finished_at": "2026-09-19T12:01:00+00:00",
                "status": "success",
                "error": None,
                "task": {"url": "http://example.test", "prompt": "test"},
                "agent": {"name": "skyvern", "model": "chat-gpt"},
                "agent_result": {
                    "adapter": "skyvern",
                    "command": ["python", "-c", "very large inline source"],
                    "returncode": 0,
                    "duration_seconds": 1.0,
                    "task_success": True,
                    "task_status": "completed",
                    "execution_success": True,
                },
            }
            (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (agent / "result.json").write_text(
                json.dumps({"output": "done", "task_success": True, "task_status": "completed"}),
                encoding="utf-8",
            )
            (agent / "stdout.log").write_text("ok\n", encoding="utf-8")
            (agent / "stderr.log").write_text("", encoding="utf-8")
            (recordings / "old.mp4").write_bytes(b"video")
            (screenshots / "old.png").write_bytes(b"image")
            for index, name in enumerate(
                ("l1_http_tls.json", "l2_browser_static.json", "l3_browser_dynamic.json", "l4_agent_trace.json"),
                start=1,
            ):
                (fingerprints / name).write_text(
                    json.dumps({"level": index, "agent_result": manifest["agent_result"]}),
                    encoding="utf-8",
                )
            (fingerprints / "traffic.pcap").write_bytes(b"complete-pcap")

            preview = migrate(source, root, apply=False)
            self.assertEqual(preview["status"], "planned")
            self.assertTrue(source.is_dir())

            report = migrate(source, root, apply=True)
            self.assertEqual(report["status"], "migrated")
            self.assertFalse(source.exists())
            target = root / "2026-09-19" / "skyvern_example"
            migrated = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema"], "agent-fingerprint-run/v2")
            self.assertEqual(migrated["outcome"]["output"], "done")
            self.assertNotIn("command", migrated["execution"])
            self.assertEqual(
                (target / "artifacts" / "network" / "traffic.pcap").read_bytes(),
                b"complete-pcap",
            )
            self.assertEqual(
                (target / "artifacts" / "recordings" / "recording.mp4").read_bytes(),
                b"video",
            )
            self.assertEqual(
                (target / "artifacts" / "screenshots" / "step.png").read_bytes(),
                b"image",
            )
            self.assertTrue((root / "index.jsonl").is_file())
            self.assertEqual(resolve_run_dir("skyvern_example", root), target)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
