"""Optional real-browser recovery regression; no model service is required."""
import asyncio
import runpy
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

from agent_fingerprint.collection.sandbox import create_server
from agent_fingerprint.adapters.webvoyager_adapter import _matching_selenium_pair

ROOT = Path(__file__).resolve().parents[1]
GUARD = runpy.run_path(str(ROOT / 'src/agent_fingerprint/runners/skyvern.py'))['_flight_input_guard']


@unittest.skipIf(async_playwright is None, 'Playwright is not installed')
class SkyvernBrowserRecoveryTest(unittest.TestCase):
    def test_date_recovery_and_flight_selection(self):
        server = create_server(port=0, directory=ROOT / 'sandbox/flights', inject_monitor=False, run_id='recovery-test')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(self.exercise(server.server_port))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def exercise(self, port):
        async with async_playwright() as pw:
            pair = _matching_selenium_pair()
            browser = await pw.chromium.launch(
                headless=True, args=['--no-sandbox'],
                executable_path=str(pair[0]) if pair else None,
            )
            try:
                page = await browser.new_page()
                await page.goto(f'http://127.0.0.1:{port}/index.html')
                delegated = []
                async def original(*args):
                    delegated.append(args)
                    return ['original']
                def success():
                    return SimpleNamespace(success=True)
                def failure(exc):
                    return SimpleNamespace(success=False, exception_message=str(exc))
                async def fill(field, text):
                    action = SimpleNamespace(element_id='test', text=text)
                    scraped = SimpleNamespace(id_to_element_dict={'test': {'frame': 'main.frame', 'attributes': {'id': field}}})
                    return await GUARD(action, page, scraped, None, None, original, success, failure)

                # Reproduce why a focus/type strategy fails for this widget.
                await page.locator('#flight-date').focus()
                await page.keyboard.type('2026-10-12')
                self.assertEqual(await page.locator('#flight-date').input_value(), '')
                result = (await fill('from', 'San Francisco'))[0]
                self.assertTrue(result.success, getattr(result, 'exception_message', ''))
                result = (await fill('to', 'New York'))[0]
                self.assertTrue(result.success, getattr(result, 'exception_message', ''))
                # Unknown dates must not silently select today or submit.
                result = (await fill('flight-date', '2099-01-01'))[0]
                self.assertFalse(result.success)
                self.assertTrue(result.skip_remaining_actions)
                self.assertTrue(await page.locator('#calendar').is_visible())
                self.assertEqual(await page.locator('#flight-date').input_value(), '')
                self.assertTrue((await fill('flight-date', 'today'))[0].success)
                self.assertTrue(await page.locator('#flight-date').input_value())
                await page.locator('#search-btn').click()
                await page.locator('#flight8').click()
                self.assertIn('UA800', await page.locator('#flight8').inner_text())
                self.assertTrue(await page.locator('#next-btn').is_enabled())
                await page.locator('#next-btn').click()
                await page.locator('#seatNumber').wait_for()
                self.assertFalse(delegated)

                # A different field or fixture must retain the upstream handler.
                self.assertEqual(await fill('seatNumber', '20'), ['original'])
                self.assertEqual(len(delegated), 1)
                await page.set_content('<input id="from"><input id="to">')
                self.assertEqual(await fill('from', 'Berlin'), ['original'])
                self.assertEqual(len(delegated), 2)
            finally:
                await browser.close()
