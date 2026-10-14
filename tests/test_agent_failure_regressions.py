"""Regressions for the isolated WebVoyager/Skyvern startup and model guards."""
import asyncio
import json
import runpy
from pathlib import Path

import pytest

from agent_fingerprint.adapters import webvoyager_adapter as voyager

RUNNER = Path(__file__).resolve().parents[1] / 'src/agent_fingerprint/runners/skyvern.py'


def cache_binary(home, kind, version, platform='linux64'):
    name = 'chrome' if kind == 'chrome' else 'chromedriver'
    path = home / '.cache' / 'selenium' / kind / platform / version / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_pins_pair_despite_newer_unmatched_browser(monkeypatch, tmp_path):
    chrome = cache_binary(tmp_path, 'chrome', '153.0.8010.52')
    driver = cache_binary(tmp_path, 'chromedriver', '153.0.8010.52')
    cache_binary(tmp_path, 'chrome', '155.0.8059.39')
    cache_binary(tmp_path, 'chromedriver', '155.0.8059.39', 'mac-arm64')
    cache_binary(tmp_path, 'chrome', '155.0.8059.39', 'mac-arm64')
    monkeypatch.setattr(voyager.Path, 'home', lambda: tmp_path)
    env = {'SE_OFFLINE': 'false', 'OTHER_AGENT_SETTING': 'unchanged'}
    voyager._configure_cached_browser(env)
    assert env == {'SE_OFFLINE': 'false', 'OTHER_AGENT_SETTING': 'unchanged',
                   'WEBVOYAGER_CHROME_BINARY': str(chrome), 'WEBVOYAGER_CHROMEDRIVER': str(driver)}


@pytest.mark.parametrize('key', ['WEBVOYAGER_CHROME_BINARY', 'WEBVOYAGER_CHROMEDRIVER', 'CHROME_BINARY', 'CHROMEDRIVER'])
def test_explicit_browser_selection_is_preserved(monkeypatch, key):
    def unexpected():
        pytest.fail('must not discover a pair when a binary is explicitly selected')
    monkeypatch.setattr(voyager, '_matching_selenium_pair', unexpected)
    env = {key: '/explicit/binary'}
    voyager._configure_cached_browser(env)
    assert env == {key: '/explicit/binary'}


def test_no_matching_cache_keeps_normal_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(voyager.Path, 'home', lambda: tmp_path)
    env = {}
    voyager._configure_cached_browser(env)
    assert env == {}


def test_only_auxiliary_calls_are_bounded():
    namespace = runpy.run_path(str(RUNNER))
    calls = []
    async def handler(*args, **kwargs):
        calls.append((args, kwargs))
        return {'field': 'city'}
    defaults = {'max_tokens': 8192, 'temperature': 0.2}
    wrapped = namespace['_wrap_qwen_llm_handler'](handler, defaults, 512)
    assert asyncio.run(wrapped(prompt='input', prompt_name='parse-input-or-select-context')) == {'field': 'city'}
    assert calls[-1][1]['parameters'] == {'max_tokens': 512, 'temperature': 0.2}
    params = {'max_completion_tokens': 128}
    asyncio.run(wrapped('input', 'parse-input-or-select-context', parameters=params))
    assert calls[-1][1]['parameters'] == {'max_tokens': 128, 'temperature': 0.2}
    assert params == {'max_completion_tokens': 128}
    asyncio.run(wrapped(prompt='input', prompt_name='extract-actions'))
    assert 'parameters' not in calls[-1][1]
    assert defaults == {'max_tokens': 8192, 'temperature': 0.2}


def test_auxiliary_prompt_contains_valid_json_example():
    prompt = runpy.run_path(str(RUNNER))['_qwen_context_prompt']('AAAD', 'Fill city', '<input id="AAAD">')
    lines = prompt.splitlines()
    example = json.loads(lines[1])
    assert 'thought' not in example
    assert isinstance(example['is_location_input'], bool)
    context = json.loads(lines[-1])
    assert context['element_id'] == 'AAAD'
    assert context['html'] == '<input id="AAAD">'


@pytest.mark.parametrize('model,local,expected', [
    ('qwen3.5-9b', True, True), ('openai/qwen3.5-27b', True, True),
    ('chat-gpt', True, False), ('glm-4.6v-flash', True, False),
    ('qwen3.5-9b', False, False),
])
def test_skyvern_browser_override_is_agent_model_scoped(monkeypatch, tmp_path, model, local, expected):
    from agent_fingerprint.adapters.skyvern_adapter import SkyvernAdapter
    for key in ('SKYVERN_CHROME_EXECUTABLE_PATH', 'CHROME_EXECUTABLE_PATH'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(voyager, '_matching_selenium_pair', lambda: (Path('/cached/chrome'), Path('/cached/driver')))
    captured = {}
    def execute(self, *args, **kwargs):
        captured.update(kwargs['env'])
    monkeypatch.setattr(SkyvernAdapter, 'execute', execute)
    SkyvernAdapter(conda_env=None, llm_model=model, llm_api_base='http://localhost:4000/v1' if local else None).run_task(
        'http://localhost:8000/index.html', 'book flight', tmp_path)
    assert ('SKYVERN_CHROME_EXECUTABLE_PATH' in captured) is expected
    if expected:
        assert captured['SKYVERN_CHROME_EXECUTABLE_PATH'] == '/cached/chrome'
    assert 'CHROME_EXECUTABLE_PATH' not in captured


def test_skyvern_explicit_browser_is_preserved(monkeypatch, tmp_path):
    from agent_fingerprint.adapters.skyvern_adapter import SkyvernAdapter
    monkeypatch.setenv('SKYVERN_CHROME_EXECUTABLE_PATH', '/explicit/chrome')
    def unexpected():
        pytest.fail('explicit Skyvern browser must bypass discovery')
    monkeypatch.setattr(voyager, '_matching_selenium_pair', unexpected)
    captured = {}
    monkeypatch.setattr(SkyvernAdapter, 'execute', lambda self, *a, **kw: captured.update(kw['env']))
    SkyvernAdapter(conda_env=None, llm_model='qwen3.5-9b', llm_api_base='http://localhost:4000/v1').run_task(
        'http://localhost:8000/index.html', 'book flight', tmp_path)
    assert captured['SKYVERN_CHROME_EXECUTABLE_PATH'] == '/explicit/chrome'


@pytest.mark.parametrize('model,wrapped', [('qwen3.5-9b', True), ('openai/qwen3.5-27b', True), ('chat-gpt', False), ('glm-4.6v-flash', False), (None, False)])
def test_webvoyager_qwen_launcher_is_scoped(monkeypatch, tmp_path, model, wrapped):
    commands = []
    monkeypatch.setattr(voyager.WebVoyagerAdapter, 'execute', lambda self, command, **kw: commands.append(command))
    voyager.WebVoyagerAdapter(conda_env=None, api_model=model).run_task('http://localhost/index.html', 'Task', tmp_path)
    runner = str(Path(voyager.__file__).resolve().parents[1] / 'runners/webvoyager.py')
    assert (runner in commands[0]) is wrapped
    assert str(voyager.DEFAULT_REPOSITORY / 'run.py') in commands[0]
