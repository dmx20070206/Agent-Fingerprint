"""Exercise the standalone runner's SDK configuration without a model service."""
import asyncio
import runpy
import sys
import types
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / 'src/agent_fingerprint/runners/skyvern.py'


@pytest.mark.parametrize('model,expected', [('qwen3.5-9b', True), ('openai/qwen3.5-27b', True), ('chat-gpt', False)])
def test_runner_generation_config(monkeypatch, tmp_path, model, expected):
    captured = {}

    class StopAfterConfig(Exception):
        pass

    class Skyvern:
        @staticmethod
        def local(**kwargs):
            captured.update(kwargs['llm_config'])
            raise StopAfterConfig

    package = types.ModuleType('skyvern')
    package.Skyvern = Skyvern
    schema = types.ModuleType('skyvern.schemas.llm')
    schema.LLMConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, 'skyvern', package)
    monkeypatch.setitem(sys.modules, 'skyvern.schemas.llm', schema)
    for key in ('SKYVERN_LLM_PROTOCOL', 'SKYVERN_LLM_MAX_TOKENS', 'SKYVERN_QWEN_ENABLE_THINKING'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AGENT_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setenv('SKYVERN_LLM_API_BASE', 'http://localhost:4000/v1')
    monkeypatch.setenv('SKYVERN_LLM_MODEL', model)
    main = runpy.run_path(str(RUNNER))['main']
    installed = []
    monkeypatch.setitem(main.__globals__, '_install_qwen_guards', lambda: installed.append(True))
    with pytest.raises(StopAfterConfig):
        asyncio.run(main())
    assert bool(installed) is expected
    params = captured['litellm_params']
    if expected:
        assert captured['max_tokens'] == 8192
        assert params['response_format'] == {'type': 'json_object'}
        assert params['extra_body']['chat_template_kwargs']['enable_thinking'] is False
    else:
        assert 'max_tokens' not in captured
        assert 'extra_body' not in params
        assert 'response_format' not in params
    monkeypatch.setenv('SKYVERN_LLM_MAX_TOKENS', '16384')
    monkeypatch.setenv('SKYVERN_LLM_PROTOCOL', 'native')
    monkeypatch.setenv('SKYVERN_QWEN_ENABLE_THINKING', 'true')
    with pytest.raises(StopAfterConfig):
        asyncio.run(main())
    assert captured['max_tokens'] == 16384
    assert 'response_format' not in captured['litellm_params']
    monkeypatch.setenv('SKYVERN_LLM_MAX_TOKENS', '0')
    with pytest.raises(ValueError, match='positive'):
        asyncio.run(main())


def test_experiment_timeout_override(monkeypatch):
    import yaml
    from agent_fingerprint.cli.experiment import build_plan
    monkeypatch.delenv('SKYVERN_TASK_TIMEOUT', raising=False)
    config = yaml.safe_load((RUNNER.parents[3] / 'configs/experiments/default.yaml').read_text())
    rows = build_plan(config, agents=['skyvern'], models=['qwen3.5-9b'], tasks=['flights', 'shop'], repeats=1)
    assert all(row['argv'][row['argv'].index('--timeout') + 1] == '1800' for row in rows)
    monkeypatch.setenv('SKYVERN_TASK_TIMEOUT', '2400')
    rows = build_plan(config, agents=['skyvern'], models=['qwen3.5-9b'], tasks=['shop'], repeats=1)
    assert rows[0]['argv'][rows[0]['argv'].index('--timeout') + 1] == '2400'
