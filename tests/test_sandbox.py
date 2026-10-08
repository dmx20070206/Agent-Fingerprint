"""Unit tests for the local sandbox HTTP server and fixture pages."""

from __future__ import annotations

import threading
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from agent_fingerprint.collection.sandbox import MONITOR_ENDPOINT, STATIC_DIR, TraceStore, create_server

PROJECT_ROOT = STATIC_DIR.parent.parent


class SandboxServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"
        # CI environments often export an HTTP proxy.  Requests to this local
        # fixture must bypass it, otherwise the proxy may return 502 instead of
        # reaching the test server.
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, path: str) -> tuple[int, str, str]:
        response = self.opener.open(Request(self.base_url + path, method="GET"), timeout=3)
        with response:
            return response.status, response.headers.get_content_type(), response.read().decode("utf-8")

    def test_static_directory_contains_diverse_pages(self) -> None:
        pages = sorted(STATIC_DIR.glob("*.html"))
        self.assertGreaterEqual(len(pages), 5)
        names = {page.name for page in pages}
        self.assertIn("01-minimal.html", names)
        self.assertIn("02-dense.html", names)
        self.assertIn("04-maze.html", names)
        self.assertIn("06-delayed-cookie.html", names)

    def test_fixture_profiles_cover_density_depth_and_timing(self) -> None:
        dense = (STATIC_DIR / "02-dense.html").read_text(encoding="utf-8")
        maze = (STATIC_DIR / "04-maze.html").read_text(encoding="utf-8")
        delayed = (STATIC_DIR / "06-delayed-cookie.html").read_text(encoding="utf-8")
        self.assertIn("i<=500", dense)
        self.assertIn("i<=20", maze)
        self.assertIn("setTimeout", delayed)
        self.assertIn("setInterval", delayed)

    def test_input_interaction_pages_have_distinct_success_contracts(self) -> None:
        contracts = {
            "mouse-move.html": ("pointerenter", "通过：鼠标航迹有效"),
            "11-mouse-click.html": ("'click'", "通过：点击序列正确"),
            "12-keyboard-type.html": ("'input'", "通过：转录完全一致"),
            "13-wheel-scroll.html": ("'wheel'", "通过：滚轮轨迹与页面深度均已确认"),
        }
        for filename, expected in contracts.items():
            body = (STATIC_DIR / filename).read_text(encoding="utf-8")
            self.assertIn('data-task-status="pending"', body)
            self.assertIn(expected[0], body)
            self.assertIn(expected[1], body)

    def test_input_interaction_tasks_target_their_matching_pages(self) -> None:
        expected = {
            "mouse_move.jsonl": "/mouse-move.html",
            "mouse_click.jsonl": "/11-mouse-click.html",
            "keyboard_type.jsonl": "/12-keyboard-type.html",
            "wheel_scroll.jsonl": "/13-wheel-scroll.html",
        }
        for filename, url in expected.items():
            task = json.loads((PROJECT_ROOT / "tasks" / filename).read_text(encoding="utf-8"))
            self.assertEqual(task["url"], url)
            self.assertTrue(task["prompt"])

    def test_serves_page_and_correct_content_type(self) -> None:
        status, content_type, body = self.get("/01-minimal.html")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html")
        self.assertIn("极简控制台", body)

    def test_root_lists_fixture_pages(self) -> None:
        status, content_type, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html")
        self.assertIn("01-minimal.html", body)

    def test_missing_page_returns_404(self) -> None:
        with self.assertRaises(HTTPError) as error:
            self.get("/does-not-exist.html")
        self.assertEqual(error.exception.code, 404)

    def test_site_collection_endpoint_is_acknowledged(self) -> None:
        request = Request(
            self.base_url + "/start",
            data=b'{"webpage":"shop"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"ok": True})

    def test_split_websites_can_each_be_served_as_sandbox_root(self) -> None:
        sandbox_root = STATIC_DIR.parent
        expectations = {
            "flights": "Flight Booking",
            "forums": "Community Forums",
            "shop": "Web Agent Shop",
        }
        for directory, title in expectations.items():
            server = create_server(port=0, directory=sandbox_root / directory, inject_monitor=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                opener = build_opener(ProxyHandler({}))
                with opener.open(f"http://{host}:{port}/index.html", timeout=3) as response:
                    body = response.read().decode("utf-8")
                self.assertIn(title, body)
                self.assertIn("__AGENT_FINGERPRINT_CONFIG__", body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_path_traversal_is_not_served(self) -> None:
        # The URL is intentionally encoded so urllib does not normalize it.
        with self.assertRaises(HTTPError) as error:
            self.get("/%2e%2e/%2e%2e/etc/passwd")
        self.assertIn(error.exception.code, {403, 404})

    def test_injected_monitor_and_trace_endpoint(self) -> None:
        store = TraceStore()
        server = create_server(port=0, trace_store=store, inject_monitor=True, run_id="inject-test")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            opener = build_opener(ProxyHandler({}))
            base = f"http://{host}:{port}"
            with opener.open(base + "/01-minimal.html", timeout=3) as response:
                body = response.read().decode("utf-8")
            self.assertTrue(body.lstrip().lower().startswith("<!doctype html>"))
            self.assertIn("__AGENT_FINGERPRINT_CONFIG__", body)
            self.assertIn("/__agent_fingerprint__/monitor.js", body)
            self.assertIn('"interaction_delay_ms": 0', body)

            payload = {
                "run_id": "inject-test",
                "session_id": "s1",
                "events": [{"seq": 1, "type": "click", "epoch_ms": None}],
            }
            request = Request(
                base + MONITOR_ENDPOINT,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                acknowledgement = json.loads(response.read().decode("utf-8"))
            self.assertEqual(acknowledgement["ack_seq"], 1)
            self.assertFalse(acknowledgement["finalized"])
            with opener.open(base + MONITOR_ENDPOINT + "?run_id=inject-test", timeout=3) as response:
                trace = json.loads(response.read().decode("utf-8"))
            self.assertEqual(trace["event_count"], 1)
            self.assertEqual(trace["events"][0]["type"], "click")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_protocol_v2_acknowledges_only_contiguous_events_and_finalizes(self) -> None:
        store = TraceStore()
        second = store.receive(
            {
                "protocol_version": 2,
                "run_id": "ack-test",
                "session_id": "browser-1",
                "events": [{"seq": 2, "type": "click"}],
            }
        )
        self.assertEqual(second["ack_seq"], 0)

        first = store.receive(
            {
                "protocol_version": 2,
                "run_id": "ack-test",
                "session_id": "browser-1",
                "events": [{"seq": 1, "type": "monitor_start"}],
            }
        )
        self.assertEqual(first["ack_seq"], 2)
        self.assertEqual(store.request_finalize("ack-test"), {"browser-1"})

        final = store.receive(
            {
                "protocol_version": 2,
                "run_id": "ack-test",
                "session_id": "browser-1",
                "events": [],
                "final": True,
                "final_seq": 2,
            }
        )
        self.assertTrue(final["finalized"])
        self.assertTrue(store.wait_for_finalization("ack-test", {"browser-1"}, timeout=0)["complete"])
        self.assertEqual(store.export("ack-test")["event_count"], 2)

    def test_interaction_delay_is_configurable(self) -> None:
        server = create_server(
            port=0,
            inject_monitor=True,
            interaction_delay_seconds=1.25,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            opener = build_opener(ProxyHandler({}))
            with opener.open(f"http://{host}:{port}/mouse-click.html", timeout=3) as response:
                body = response.read().decode("utf-8")
            self.assertIn('"interaction_delay_ms": 1250', body)
            self.assertLess(body.index("__AGENT_FINGERPRINT_CONFIG__"), body.index("<title>"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
