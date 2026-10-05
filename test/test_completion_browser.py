"""Optional real-browser coverage for page contracts and their HTTP collector.

Run with a Playwright environment; PLAYWRIGHT_CHROMIUM_EXECUTABLE can select
an existing Chromium binary. No model API is used.
"""
import os
import threading
import time
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from sandbox.server import create_server


@unittest.skipIf(sync_playwright is None, 'Playwright is not installed')
class CompletionBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        options = {'headless': True, 'args': ['--no-sandbox']}
        if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'):
            options['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']
        try:
            cls.browser = cls.playwright.chromium.launch(**options)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def open_site(self, site):
        root = Path(__file__).resolve().parents[1] / 'sandbox' / site
        self.server = create_server(port=0, directory=root, inject_monitor=False, run_id='browser-test')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.page = self.browser.new_page()
        self.page.set_default_timeout(8000)
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/index.html')
        self.addCleanup(self.close_site)
        self.wait_status('failed')  # The actual page reports pending.

    def close_site(self):
        self.page.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def wait_status(self, status):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.server.completion_verifier.result('browser-test').status == status:
                return
            self.page.wait_for_timeout(25)
        self.assertEqual(self.server.completion_verifier.result('browser-test').status, status)

    def test_flight_submission_accepts_different_field_choices(self):
        self.open_site('flights')
        page = self.page
        page.locator('#from').fill('San Francisco')
        page.locator('#to').fill('New York')
        page.locator('#flight-date').click()
        page.locator('#calendar-today').click()
        page.locator('#search-btn').click()
        page.locator('#flight8').click()
        page.locator('#next-btn').click()
        # Deliberately differ from the audited 20E / carry-on / Joe Jones task.
        page.locator('#seatNumber').select_option('12')
        page.locator('#seatLetter').select_option('A')
        page.locator('#ticketType').select_option('Economy')
        page.locator('#next-btn').click()
        for key, value in {'name': 'Other Traveler', 'email': 'other@example.com', 'phone': '123-456-7890'}.items():
            page.locator('#' + key).fill(value)
        page.locator('input[name=sex][value=M]').check()
        page.locator('#next-btn').click()
        page.locator('#cardNumber').fill('9685 6548 2374 3495')
        page.locator('#cardName').fill('Other Traveler')
        page.locator('#cardName').press('Enter')
        self.wait_status('failed')
        self.assertTrue(page.locator('#book-btn').is_disabled())
        page.locator('#securityCode').fill('123')
        self.wait_status('failed')
        page.locator('#book-btn').click()
        self.wait_status('passed')
        self.assertIn('successfully booked', page.locator('body').inner_text())
        page.reload()
        self.wait_status('failed')

    def test_shop_requires_nonempty_cart_result_page(self):
        self.open_site('shop')
        page = self.page
        page.locator('#cart-button').click()
        page.wait_for_timeout(300)
        self.wait_status('failed')
        page.locator('#cart-back-btn').click()
        page.locator('#search-input').fill('keyboards')
        page.locator('#search-form button[type=submit]').click()
        page.get_by_role('button', name='Add to Cart').first.click()
        self.wait_status('failed')
        page.locator('#cart-button').click()
        self.wait_status('passed')
        page.get_by_role('button', name='Remove', exact=True).click()
        self.wait_status('failed')

    def test_forum_reply_is_verified_after_fragment_navigation(self):
        self.open_site('forums')
        page = self.page
        page.locator('a[data-thread-id]').first.click()
        page.locator('.reply-btn').first.click()
        page.locator('.reply-form:not(.hidden) textarea').fill('A test reply with different wording.')
        page.locator('.reply-form:not(.hidden) button[type=submit]').click()
        self.wait_status('passed')
        page.locator('a[data-route="/"]').click()
        self.wait_status('failed')
