"""Real Selenium regression coverage; runs in the webvoyager environment."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

REPOSITORY = Path(__file__).resolve().parents[1] / 'lib' / 'WebVoyager'
sys.path.insert(0, str(REPOSITORY))

try:
    import run as voyager
    from interaction import NoProgressGuard, describe_select, page_state, select_option
    from utils import extract_information, get_web_element_rect, get_webarena_accessibility_tree
except ModuleNotFoundError:
    voyager = None

HTML = '''<label for="origin">Departure</label>
<select id="origin"><option value="pek">Beijing</option>
<option value="sha">Shanghai</option><option disabled value="x">Disabled</option></select>
<input id="name"><button id="noop">No-op</button>
<script>window.events=[]; origin=document.getElementById('origin');
 document.getElementById('origin').addEventListener('input',e=>events.push(e.type));
 document.getElementById('origin').addEventListener('change',e=>events.push(e.type));</script>'''


@unittest.skipIf(voyager is None, 'Run in the webvoyager environment with its dependencies installed')
class InteractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = voyager.webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        cache = Path.home() / '.cache/selenium'
        pairs = [(p, cache / 'chrome/linux64' / p.parent.name / 'chrome')
                 for p in cache.glob('chromedriver/linux64/*/chromedriver')]
        pair = next(((d, c) for d, c in pairs if c.is_file()), None)
        if pair:
            options.binary_location = str(pair[1])
            cls.driver = voyager.webdriver.Chrome(service=voyager.Service(str(pair[0])), options=options)
        else:
            cls.driver = voyager.create_driver(options)
        cls.driver.set_window_size(1024, 768)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        self.url = 'data:text/html;charset=utf-8,' + quote(HTML)
        self.driver.get(self.url)
        self.select = self.driver.find_element('id', 'origin')

    def test_observation_selection_events_and_invalid_options(self):
        rects, elements, text = get_web_element_rect(self.driver)
        self.assertIn(self.select, elements)
        self.assertIn('<select>', text)
        self.assertIn('"selected": [{"text": "Beijing", "value": "pek"}]', text)
        for rect in rects:
            self.driver.execute_script('arguments[0].remove()', rect)
        before = page_state(self.driver)
        select_option(self.driver, self.select, 'Shanghai')
        self.assertNotEqual(before, page_state(self.driver))
        self.assertEqual(self.driver.execute_script('return window.events'), ['change'])
        select_option(self.driver, self.select, 'sha')
        self.assertEqual(self.driver.execute_script('return window.events'), ['change'])
        self.assertEqual(json.loads(describe_select(self.driver, self.select))['value'], 'sha')
        for invalid in ('Missing', 'Disabled'):
            with self.assertRaises(ValueError):
                select_option(self.driver, self.select, invalid)
        self.assertEqual(self.select.get_attribute('value'), 'sha')
        tree, nodes = get_webarena_accessibility_tree(self.driver)
        self.assertIn('\"selected\": [{\"text\": \"Shanghai\", \"value\": \"sha\"}]', tree)
        self.assertTrue(any('combobox' in n['text'].lower() for n in nodes.values()))

    def test_guard_tracks_fields_and_allows_changed_state(self):
        guard = NoProgressGuard()
        signature = ('click', self.select.id, '')
        state = page_state(self.driver)
        guard.record(signature, state, state)
        self.assertTrue(guard.blocked(signature, state))
        guard.record(('wait', None, ''), state, state)
        self.assertTrue(guard.blocked(signature, state))
        select_option(self.driver, self.select, 'sha')
        self.assertFalse(guard.blocked(signature, page_state(self.driver)))
        for key in ('move', 'type', 'scroll', 'select', 'wait'):
            action = (key, 'element', 'value')
            guard.record(action, state, state)
            self.assertTrue(guard.blocked(action, state))
        guard.record(('move', 'A', ''), state, state)
        guard.record(('move', 'B', ''), state, state)
        self.assertFalse(guard.blocked(('move', 'A', ''), state))

    def test_select_parser_and_type_compatibility(self):
        self.assertEqual(extract_information('Select [3]; [Shanghai]'),
                         ('select', {'number': '3', 'content': 'Shanghai'}))
        self.assertEqual(extract_information('Select 3; sha'),
                         ('select', {'number': '3', 'content': 'sha'}))
        voyager.exec_action_type({'content': 'sha'}, self.select, self.driver)
        self.assertEqual(self.select.get_attribute('value'), 'sha')

    def test_loop_recovers_with_fresh_observation(self):
        observations = []
        actions = ['Click [0]', 'Click [0]', 'Select [0]; [Missing]',
                   'Select [0]; [Missing]', 'Select [0]; [Shanghai]', 'ANSWER; done']
        def api(args, client, messages):
            observations.append(messages[-1])
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='Thought: test\nAction: ' + actions[len(observations) - 1]))])
            return 1, 1, False, response
        # Keep the browser available after main's cleanup for assertions.
        with tempfile.TemporaryDirectory() as folder:
            task_file = Path(folder) / 'task.jsonl'
            task_file.write_text(json.dumps({'id': 'test', 'web': self.url, 'ques': 'select Shanghai'}))
            argv = ['run.py', '--test_file', str(task_file), '--output_dir', folder,
                    '--download_dir', str(Path(folder) / 'downloads'), '--max_iter', '6', '--headless']
            with patch.object(sys, 'argv', argv), patch.object(voyager, 'create_driver', return_value=self.driver), \
                 patch.object(voyager, '_safe_quit_driver'), patch.object(voyager, 'call_gpt4v_api', side_effect=api), \
                 patch.object(voyager.time, 'sleep'):
                voyager.main()
            self.assertEqual(self.driver.find_element('id', 'origin').get_attribute('value'), 'sha')
            recovery = observations[2]['content']
            self.assertIn('Recovery:', recovery[0]['text'])
            self.assertIn('"value": "pek"', recovery[0]['text'])
            self.assertEqual(recovery[1]['type'], 'image_url')
            self.assertIn('Action failed:', observations[3]['content'][0]['text'])
            self.assertIn('Recovery:', observations[4]['content'][0]['text'])
            self.assertTrue(json.loads((Path(folder) / 'result.json').read_text())['task_success'])
        voyager._ACTIVE_DRIVERS.clear()


if __name__ == '__main__':
    unittest.main()
