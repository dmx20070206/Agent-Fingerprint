"""Process-local compatibility fixes for WebVoyager with Qwen 3.5.

Loaded only by that adapter/model combination; the upstream checkout and the
shared OpenAI client implementation are never modified on disk.
"""
import importlib.util
import logging
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

GUIDANCE = """
For a date requested as today, use the browser-local date in the observation.
Never substitute the first available day. Numerical element labels are not day
numbers. Open the calendar and select Today, scrolling to reveal it if needed.
For nested scroll areas, Scroll [label]; down over a labeled child scrolls its
ancestor list. Scroll WINDOW only moves the outer page. Inspect scroll hints and
change the scroll target if the view does not change. Never click disabled Next;
select the requested flight from actual results first.
"""

SCROLL_STATE = """
const e = arguments[0];
let p = e;
while (p && p !== document.body && p !== document.documentElement) {
    const style = getComputedStyle(p);
    if (/(auto|scroll)/.test(style.overflowY) && p.scrollHeight > p.clientHeight + 2)
        return {top:p.scrollTop, max:p.scrollHeight-p.clientHeight};
    p = p.parentElement;
}
return null;
"""

SCROLL_CANDIDATES = """
return Array.from(document.querySelectorAll('*')).filter(e => {
    if (e === document.body || e === document.documentElement) return false;
    const s = getComputedStyle(e), r = e.getBoundingClientRect();
    const room = arguments[0] > 0 ? e.scrollHeight-e.clientHeight-e.scrollTop : e.scrollTop;
    const x = Math.max(0,r.left), y = Math.max(0,r.top);
    const right = Math.min(innerWidth,r.right), bottom = Math.min(innerHeight,r.bottom);
    if (!/(auto|scroll)/.test(s.overflowY) || room < 2 || right-x < 20 || bottom-y < 20) return false;
    const hit = document.elementFromPoint((x+right)/2,(y+bottom)/2);
    return hit && (hit === e || e.contains(hit));
}).map(e => {
    const r = e.getBoundingClientRect();
    return {x:Math.floor((Math.max(0,r.left)+Math.min(innerWidth,r.right))/2),
            y:Math.floor((Math.max(0,r.top)+Math.min(innerHeight,r.bottom))/2)};
});
"""


def generation_options():
    budget = int(os.environ.get('WEBVOYAGER_QWEN_MAX_TOKENS', '2048'))
    if budget <= 0:
        raise ValueError('WEBVOYAGER_QWEN_MAX_TOKENS must be positive')
    return {'max_tokens': budget, 'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}}


def requests_today(messages):
    # Only the initial task, not text supplied by a later webpage observation.
    for message in messages:
        if message.get('role') == 'user':
            content = message.get('content', '')
            if isinstance(content, list):
                content = '\n'.join(x.get('text', '') for x in content if x.get('type') == 'text')
            return bool(re.search(r'\bDate\s*:\s*today\b', content, re.I))
    return False


def install(voyager):
    options = generation_options()
    state = {'today_requested': False}
    original_api = voyager.call_gpt4v_api
    original_observe = voyager.get_web_element_rect
    original_scroll = voyager.exec_action_scroll
    original_click = voyager.exec_action_click
    voyager.SYSTEM_PROMPT += GUIDANCE
    voyager.SYSTEM_PROMPT_TEXT_ONLY += GUIDANCE

    def call_api(args, client, messages):
        state['today_requested'] = requests_today(messages)

        def create(**kwargs):
            kwargs.update(options)
            response = client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            usage = getattr(response, 'usage', None)
            logging.info('Qwen response: finish_reason=%s completion_tokens=%s content_present=%s reasoning_present=%s',
                         getattr(choice, 'finish_reason', None), getattr(usage, 'completion_tokens', None),
                         bool(getattr(choice.message, 'content', None)),
                         bool(getattr(choice.message, 'reasoning_content', None) or getattr(choice.message, 'reasoning', None)))
            return response

        proxy = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return original_api(args, proxy, messages)

    def observe(driver, *args, **kwargs):
        rects, elements, text = original_observe(driver, *args, **kwargs)
        today = driver.execute_script("""const d=new Date(); return [d.getFullYear(),
            String(d.getMonth()+1).padStart(2,'0'), String(d.getDate()).padStart(2,'0')].join('-');""")
        hints = [f'Browser-local today: {today}.']
        for index, element in enumerate(elements):
            scroll = driver.execute_script(SCROLL_STATE, element)
            if scroll:
                hints.append(f'[{index}] is inside a scrollable area: position {scroll["top"]}/{scroll["max"]}. '
                             f'Use Scroll [{index}]; down or up to scroll this area, not WINDOW.')
            if not element.is_enabled():
                hints.append(f'[{index}] is disabled; do not click it.')
        calendar = driver.execute_script("""
            const c=document.querySelector('#calendar'), t=document.querySelector('#calendar-today');
            if (!c || c.hidden || !t || !c.getClientRects().length) return null;
            const r=t.getBoundingClientRect();
            return {todayVisible:r.top>=0 && r.bottom<=innerHeight};
        """)
        if calendar and not calendar['todayVisible']:
            hints.append('The open calendar has a Today button below/above the viewport. Scroll WINDOW to reveal it; do not pick another date.')
        return rects, elements, text + '\n' + '\n'.join(hints)

    def scroll(info, elements, driver, args, obs_info):
        before = driver.execute_script('return [window.scrollX, window.scrollY]')
        result = original_scroll(info, elements, driver, args, obs_info)
        if info['number'] != 'WINDOW':
            return result
        after = driver.execute_script('return [window.scrollX, window.scrollY]')
        if before != after:
            return result
        delta = args.window_height * 2 // 3 * (1 if info['content'] == 'down' else -1)
        candidates = driver.execute_script(SCROLL_CANDIDATES, delta)
        if len(candidates) == 1:
            point = candidates[0]
            origin = voyager.ScrollOrigin.from_viewport(point['x'], point['y'])
            voyager.ActionChains(driver).scroll_from_origin(origin, 0, delta).perform()
            voyager.time.sleep(0.3)
            logging.info('Scroll recovery: outer page at boundary; scrolled the unique visible inner area using wheel input')
        elif candidates:
            raise ValueError('Window cannot scroll further; multiple inner areas exist. Use Scroll [label] on a child of the desired area.')
        return result

    def click(info, element, driver):
        if state['today_requested'] and urlsplit(driver.current_url).hostname in {'127.0.0.1', 'localhost', '::1'}:
            wrong_day = driver.execute_script(r"""
                const e=arguments[0], d=new Date(), c=document.querySelector('#calendar');
                if (!document.querySelector('#search-form #flight-date') || !c || !c.contains(e) || !/^day-\d+$/.test(e.id)) return false;
                const month=document.querySelector('#monthLabel');
                return Number(e.textContent)!==d.getDate() || !month ||
                    month.textContent!==d.toLocaleString('default',{month:'long',year:'numeric'});
            """, element)
            if wrong_day:
                raise ValueError('The requested date is today; this calendar day is different. Select Today, scrolling WINDOW to reveal it if necessary.')
        return original_click(info, element, driver)

    voyager.call_gpt4v_api = call_api
    voyager.get_web_element_rect = observe
    voyager.exec_action_scroll = scroll
    voyager.exec_action_click = click


def main():
    run_script = Path(sys.argv.pop(1)).resolve()
    sys.path.insert(0, str(run_script.parent))
    spec = importlib.util.spec_from_file_location('_webvoyager_upstream', run_script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    install(module)
    return module.main()


if __name__ == '__main__':
    raise SystemExit(main())
