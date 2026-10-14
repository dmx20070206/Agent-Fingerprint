"""Model-scoped request and real Selenium regressions for the Qwen runner."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from agent_fingerprint.runners import webvoyager as fixes

ROOT = Path(__file__).resolve().parents[1]


class QwenRequestTest(unittest.TestCase):
    def test_generation_and_initial_task_detection(self):
        with patch.dict(os.environ, {'WEBVOYAGER_QWEN_MAX_TOKENS': '2048'}):
            self.assertEqual(fixes.generation_options(), {'max_tokens': 2048, 'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}})
        with patch.dict(os.environ, {'WEBVOYAGER_QWEN_MAX_TOKENS': '0'}):
            with self.assertRaises(ValueError):
                fixes.generation_options()
        self.assertTrue(fixes.requests_today([{'role':'user','content':[{'type':'text','text':'Book flight. Date: today'}]}]))
        self.assertFalse(fixes.requests_today([{'role':'user','content':'Book for 2099-01-01'}, {'role':'user','content':'Date: today'}]))

    def test_wrapper_forwards_request_and_preserves_client(self):
        calls = []
        response = NS(choices=[NS(finish_reason='stop',message=NS(content='Action: Wait'))],usage=NS(completion_tokens=5))
        def create(**kwargs):
            calls.append(kwargs)
            return response
        client = NS(chat=NS(completions=NS(create=create)))
        def original(args, proxy, messages):
            return proxy.chat.completions.create(model='qwen3.5-9b',messages=messages,max_tokens=1000,seed=3,timeout=17)
        module = NS(call_gpt4v_api=original,get_web_element_rect=lambda *a:None,
                    exec_action_scroll=lambda *a:None,exec_action_click=lambda *a:None,
                    SYSTEM_PROMPT='original',SYSTEM_PROMPT_TEXT_ONLY='original')
        with patch.dict(os.environ, {'WEBVOYAGER_QWEN_MAX_TOKENS': '2048'}):
            fixes.install(module)
        messages=[{'role':'user','content':'Date: today'}]
        self.assertIs(module.call_gpt4v_api(NS(), client, messages), response)
        self.assertIs(client.chat.completions.create, create)
        self.assertEqual(calls[0]['max_tokens'],2048)
        self.assertFalse(calls[0]['extra_body']['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(calls[0]['seed'],3)
        self.assertEqual(calls[0]['timeout'],17)
        self.assertEqual(messages,[{'role':'user','content':'Date: today'}])


try:
    import selenium
except ImportError:
    selenium = None


@unittest.skipIf(selenium is None, 'Run in the webvoyager environment')
class QwenBrowserTest(unittest.TestCase):
    def test_today_and_nested_results_scroll(self):
        from agent_fingerprint.collection.sandbox import create_server
        from agent_fingerprint.adapters.webvoyager_adapter import _matching_selenium_pair
        sys.path.insert(0,str(ROOT/'third_party/WebVoyager'))
        spec=importlib.util.spec_from_file_location('_voyager_qwen_test',ROOT/'third_party/WebVoyager/run.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fixes.install(module)
        pair=_matching_selenium_pair()
        self.assertIsNotNone(pair)
        options=module.webdriver.ChromeOptions()
        options.binary_location=str(pair[0])
        for flag in ('--headless','--no-sandbox','--disable-dev-shm-usage'):
            options.add_argument(flag)
        server=create_server(port=0,directory=ROOT/'sandbox/flights',inject_monitor=False,run_id='voyager-qwen-test')
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with module.webdriver.Chrome(service=module.Service(str(pair[1])),options=options) as driver:
                driver.set_window_size(1024,768)
                driver.get(f'http://127.0.0.1:{server.server_port}/index.html')
                # Populate the runtime task state with a mocked valid API result.
                response=NS(choices=[NS(finish_reason='stop',message=NS(content='Thought: date\nAction: Click [2]'))],usage=NS(prompt_tokens=1,completion_tokens=5))
                client=NS(chat=NS(completions=NS(create=lambda **kw:response)))
                args=NS(text_only=False,seed=None,window_height=768)
                module.call_gpt4v_api(args,client,[{'role':'user','content':'Book flight. Date: today'}])
                driver.find_element('id','from').send_keys('San Francisco')
                driver.find_element('id','to').send_keys('New York')
                driver.find_element('id','flight-date').click()
                current=driver.execute_script('return new Date().getDate()')
                wrong=driver.find_element('id','day-2' if current==1 else 'day-1')
                with self.assertRaisesRegex(ValueError,'different'):
                    module.exec_action_click({},wrong,driver)
                self.assertEqual(driver.find_element('id','flight-date').get_attribute('value'),'')
                rects,els,text=module.get_web_element_rect(driver)
                self.assertIn('Browser-local today:',text)
                self.assertIn('Today button',text)
                for rect in rects:
                    driver.execute_script('arguments[0].remove()',rect)
                module.exec_action_scroll({'number':'WINDOW','content':'down'},[],driver,args,None)
                module.exec_action_click({},driver.find_element('id','calendar-today'),driver)
                self.assertEqual(driver.find_element('id','flight-date').get_attribute('value'),driver.execute_script('return new Date().toLocaleDateString()'))
                driver.find_element('id','search-btn').click()
                module.time.sleep(.3)
                for attempt in range(8):
                    rects,els,text=module.get_web_element_rect(driver)
                    self.assertIn('inside a scrollable area',text)
                    visible=any(el.get_attribute('id')=='flight8' for el in els)
                    for rect in rects:
                        driver.execute_script('arguments[0].remove()',rect)
                    if visible:
                        break
                    module.exec_action_scroll({'number':'WINDOW','content':'down'},els,driver,args,None)
                self.assertTrue(visible,'UA800 must become visible after nested scrolling')
                self.assertGreater(driver.execute_script('return document.querySelector("#flight-results").scrollTop'),0)
                module.exec_action_click({},driver.find_element('id','flight8'),driver)
                driver.find_element('id','next-btn').click()
                self.assertTrue(driver.find_element('id','seatNumber').is_displayed())
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
