"""Adapter for the open-source ``browser-use`` package.

The package is imported only in a short runner sent to the Browser-use Conda
environment.  Consequently the main pipeline does not need Browser-use (or
its LangChain dependencies) installed in its own environment.
"""

from __future__ import annotations

from pathlib import Path

from .base_adapter import AgentResult, BaseAgentAdapter

_RUNNER = r'''
import asyncio
import inspect
import json
import os
import traceback
from pathlib import Path

from browser_use import Agent, Tools
from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession


verified_page_state = {}


async def _read_page_state(session):
    response = await session.cdp_client.send.Runtime.evaluate(
        params={
            "expression": """(() => ({
              taskStatus: document.body && document.body.dataset
                ? document.body.dataset.taskStatus || null : null,
              resultText: document.querySelector('#result')
                ? document.querySelector('#result').textContent.trim() : null
            }))()""",
            "returnByValue": True,
        },
        session_id=session.session_id,
    )
    state = response.get("result", {}).get("value")
    if isinstance(state, dict):
        verified_page_state.clear()
        verified_page_state.update(state)
        return state
    return {}


async def _find_exact_text_point(session, text):
    encoded = json.dumps(str(text))
    expression = f"""
    (() => {{
      const wanted = {encoded};
      const candidates = Array.from(document.querySelectorAll('body *'))
        .map(element => ({{element, rect: element.getBoundingClientRect()}}))
        .filter(item => item.element.textContent.trim() === wanted &&
          item.rect.width > 0 && item.rect.height > 0 &&
          item.rect.bottom > 0 && item.rect.right > 0 &&
          item.rect.top < window.innerHeight && item.rect.left < window.innerWidth)
        .sort((a, b) => a.rect.width * a.rect.height - b.rect.width * b.rect.height);
      if (!candidates.length) return null;
      const rect = candidates[0].rect;
      return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
    }})()
    """
    response = await session.cdp_client.send.Runtime.evaluate(
        params={"expression": expression, "returnByValue": True},
        session_id=session.session_id,
    )
    point = response.get("result", {}).get("value")
    return point if isinstance(point, dict) else None


def _extract_json_value(content: str):
    """Return the first complete JSON value from a model response.

    Some OpenAI-compatible Gemini endpoints ignore the requested plain-JSON
    format and wrap the object in a Markdown fence (or add a short preface).
    Browser-use normally passes the entire string straight to Pydantic, which
    makes otherwise valid actions fail before they can be executed.
    """
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    decoder = json.JSONDecoder()
    for offset, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[offset:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("Model response does not contain a complete JSON value")


def _normalizing_chat_openai_class(base_class):
    class NormalizingChatOpenAI(base_class):
        async def ainvoke(self, messages, output_format=None, **kwargs):
            if output_format is None:
                return await super().ainvoke(messages, output_format, **kwargs)

            original_format = output_format

            class NormalizedOutput:
                @classmethod
                def model_json_schema(cls, *args, **schema_kwargs):
                    return original_format.model_json_schema(*args, **schema_kwargs)

                @classmethod
                def model_validate_json(cls, content, *args, **validation_kwargs):
                    value = _extract_json_value(content)
                    # Gemini occasionally shortens our one-field custom action
                    # from {"move_mouse_to_text": {"text": "1"}} to
                    # {"move_mouse_to_text": "1"}. Restore the schema-shaped
                    # object without changing the requested action or target.
                    if isinstance(value, dict):
                        actions = value.get("action")
                        if isinstance(actions, list):
                            for action in actions:
                                if not isinstance(action, dict):
                                    continue
                                for action_name in ("move_mouse_to_text", "click_mouse_on_text"):
                                    action_value = action.get(action_name)
                                    if action_value is not None and not isinstance(action_value, dict):
                                        action[action_name] = {"text": str(action_value)}
                    return original_format.model_validate(value)

            return await super().ainvoke(messages, NormalizedOutput, **kwargs)

    return NormalizingChatOpenAI


def make_llm(
    provider: str,
    model: str | None,
    api_base: str | None,
    api_key: str | None,
    upstream_model: str | None = None,
):
    # LiteLLM exposes an OpenAI-compatible endpoint.  When api_base is set we
    # intentionally use ChatOpenAI even if the upstream provider is Claude;
    # the gateway performs the provider translation.
    if api_base:
        try:
            from browser_use import ChatOpenAI
        except ImportError:
            from langchain_openai import ChatOpenAI
        # OpenAI-compatible proxies generally expose the API under /v1.  The
        # model value here is the public alias configured in LiteLLM.
        kwargs = {"model": model or "gpt-4o", "base_url": api_base}
        # Browser-use's ChatOpenAI normally forces its action schema through
        # OpenAI Structured Outputs. DeepSeek and Gemini reject or ignore that
        # response_format, while Anthropic translation through LiteLLM can
        # intermittently return prose/content=None. For those routes, put the
        # schema in the system prompt and normalize the returned JSON locally.
        # ``model`` is the public LiteLLM alias (often ``chat-gpt``), while
        # ``upstream_model`` is the provider model selected by the gateway.
        # The latter is the authoritative value for compatibility decisions:
        # a DeepSeek route must not receive OpenAI JSON-schema response_format
        # even when the public alias is deliberately provider-neutral.
        compatibility_model = (upstream_model or model or "").lower()
        needs_compatibility = (
            compatibility_model.startswith(
                ("deepseek-", "deepseek/", "gemini-", "gemini/", "claude-", "anthropic/")
            )
            or compatibility_model in {"deepseek", "gemini", "claude", "anthropic"}
        )
        if needs_compatibility:
            kwargs["dont_force_structured_output"] = True
            # Without response_format=json_schema, include the same schema in
            # the system prompt so the model still emits a parseable action.
            kwargs["add_schema_to_system_prompt"] = True
        if api_key:
            kwargs["api_key"] = api_key
        # Even OpenAI-compatible routes can prepend prose to otherwise valid
        # structured JSON. Keep their native schema request, but normalize the
        # response before validation just as for the other proxy providers.
        ChatOpenAI = _normalizing_chat_openai_class(ChatOpenAI)
        return ChatOpenAI(**kwargs)
    if provider == "browser_use":
        from browser_use import ChatBrowserUse
        return ChatBrowserUse()
    if provider == "openai":
        try:
            from browser_use import ChatOpenAI
        except ImportError:
            from langchain_openai import ChatOpenAI
        kwargs = {"model": model} if model else {}
        return ChatOpenAI(**kwargs)
    if provider == "anthropic":
        try:
            from browser_use import ChatAnthropic
        except ImportError:
            from langchain_anthropic import ChatAnthropic
        kwargs = {"model": model} if model else {}
        return ChatAnthropic(**kwargs)
    raise ValueError("BROWSER_USE_LLM_PROVIDER must be browser_use, openai, or anthropic")


def make_browser(proxy_url: str | None):
    """Create a Browser-use browser with an explicit mitm proxy when needed.

    Browser-use has changed the exact browser class export across releases;
    keep the compatibility fallbacks in the isolated child and fail with a
    clear message if the pinned version does not expose proxy settings.
    """
    if not proxy_url:
        return None
    # browser-use 0.13 calls the profile class ``BrowserProfile`` and exposes
    # ``Browser`` as an alias of ``BrowserSession``.  Older releases used a
    # ``BrowserConfig`` object.  Keep both paths in the child process so the
    # orchestrator stays independent of either dependency tree.
    try:
        from browser_use import Browser, BrowserProfile
        from browser_use.browser.profile import ProxySettings
        profile = BrowserProfile(proxy=ProxySettings(server=proxy_url, bypass=""))
        try:
            return Browser(browser_profile=profile)
        except TypeError:
            # Some transitional releases accepted the profile under ``config``.
            return Browser(config=profile)
    except (ImportError, AttributeError, TypeError):
        try:
            from browser_use.browser.browser import Browser, BrowserConfig
        except ImportError as exc:
            raise RuntimeError(
                "Browser-use proxy capture requires BrowserProfile/ProxySettings "
                "or the legacy BrowserConfig API"
            ) from exc
        config = BrowserConfig(extra_chromium_args=["--proxy-server=" + proxy_url])
        return Browser(config=config)


def make_tools():
    # JavaScript evaluation lets an Agent synthesize DOM events or directly
    # mutate task state. Keep interactions on browser input paths and provide
    # an explicit real mouse-move action for hover-only benchmark tasks.
    # The native click action must remain available for ordinary forms (for
    # example date pickers, search buttons, and flight options).
    tools = Tools(exclude_actions=["evaluate"])
    moved_texts = set()
    clicked_texts = []
    verified_page_state.clear()

    @tools.action(
        "Move the real mouse pointer, without clicking, to the smallest visible "
        "page element whose text exactly matches the supplied text. Use this for "
        "hover and mouse-movement tasks; call it once per target in the requested order."
    )
    async def move_mouse_to_text(text: str, browser_session: BrowserSession) -> ActionResult:
        normalized_text = str(text)
        if normalized_text in moved_texts:
            message = (
                f"The real mouse was already moved to text {normalized_text!r}; "
                "the duplicate physical move was suppressed. Continue with the next waypoint."
            )
            return ActionResult(extracted_content=message, long_term_memory=message)
        session = await browser_session.get_or_create_cdp_session(focus=True)
        point = await _find_exact_text_point(session, normalized_text)
        if point is None:
            return ActionResult(error=f"No visible element has exact text {text!r}")
        await session.cdp_client.send.Input.dispatchMouseEvent(
            params={
                "type": "mouseMoved",
                "x": float(point["x"]),
                "y": float(point["y"]),
                "button": "none",
                "buttons": 0,
            },
            session_id=session.session_id,
        )
        moved_texts.add(normalized_text)
        # Return read-only page evidence with the action. This lets Browser-use's
        # task judge distinguish a verified benchmark pass from an unsupported
        # final claim, without taking a screenshot or mutating DOM state.
        state = await _read_page_state(session)
        message = f"Moved the real mouse pointer to the element with text {text!r} without clicking."
        if isinstance(state, dict):
            message += (
                " Read-only page verification after the move: "
                f"taskStatus={state.get('taskStatus')!r}, resultText={state.get('resultText')!r}."
            )
        return ActionResult(extracted_content=message, long_term_memory=message)

    @tools.action(
        "Click the smallest visible page element whose text exactly matches the supplied text, "
        "using real browser mouse input. Use this for numbered signal-button tasks."
    )
    async def click_mouse_on_text(text: str, browser_session: BrowserSession) -> ActionResult:
        normalized_text = str(text)
        if normalized_text in clicked_texts:
            message = (
                f"Text {normalized_text!r} was already clicked with real mouse input; "
                f"the duplicate click was suppressed. Physical click sequence so far: {clicked_texts!r}. "
                "Continue with the next requested signal."
            )
            return ActionResult(extracted_content=message, long_term_memory=message)
        session = await browser_session.get_or_create_cdp_session(focus=True)
        point = await _find_exact_text_point(session, normalized_text)
        if point is None:
            return ActionResult(error=f"No visible element has exact text {text!r}")
        coordinates = {"x": float(point["x"]), "y": float(point["y"])}
        await session.cdp_client.send.Input.dispatchMouseEvent(
            params={"type": "mouseMoved", **coordinates, "button": "none", "buttons": 0},
            session_id=session.session_id,
        )
        await session.cdp_client.send.Input.dispatchMouseEvent(
            params={"type": "mousePressed", **coordinates, "button": "left", "buttons": 1, "clickCount": 1},
            session_id=session.session_id,
        )
        await session.cdp_client.send.Input.dispatchMouseEvent(
            params={"type": "mouseReleased", **coordinates, "button": "left", "buttons": 0, "clickCount": 1},
            session_id=session.session_id,
        )
        clicked_texts.append(normalized_text)
        state = await _read_page_state(session)
        message = (
            f"Clicked the element with exact text {normalized_text!r} using real mouse input. "
            f"Physical click sequence so far: {clicked_texts!r}. A pending page status is expected "
            "until every requested signal has been clicked. "
            f"Read-only page verification: taskStatus={state.get('taskStatus')!r}, "
            f"resultText={state.get('resultText')!r}."
        )
        return ActionResult(extracted_content=message, long_term_memory=message)

    @tools.action("Read and report the page's task status and result text without modifying the page.")
    async def verify_page_status(browser_session: BrowserSession) -> ActionResult:
        session = await browser_session.get_or_create_cdp_session(focus=True)
        state = await _read_page_state(session)
        message = (
            f"Read-only page verification: taskStatus={state.get('taskStatus')!r}, "
            f"resultText={state.get('resultText')!r}."
        )
        return ActionResult(extracted_content=message, long_term_memory=message)

    return tools


async def main():
    url = os.environ["AGENT_TASK_URL"]
    prompt = os.environ["AGENT_TASK_PROMPT"]
    output_dir = Path(os.environ["AGENT_OUTPUT_DIR"])
    # Browser-use installs signal handlers which may exit zero before run()
    # returns. Leave a failed/incomplete marker until a real verdict is saved.
    (output_dir / "result.json").write_text(
        json.dumps({"task_success": False, "task_status": "incomplete"}),
        encoding="utf-8",
    )
    provider = os.environ.get("BROWSER_USE_LLM_PROVIDER", "browser_use")
    model = os.environ.get("BROWSER_USE_LLM_MODEL") or None
    upstream_model = os.environ.get("BROWSER_USE_UPSTREAM_MODEL") or None
    api_base = os.environ.get("BROWSER_USE_API_BASE") or None
    api_key = os.environ.get("BROWSER_USE_API_KEY") or None
    proxy_url = os.environ.get("BROWSER_USE_PROXY_SERVER") or None
    max_steps = os.environ.get("BROWSER_USE_MAX_STEPS")
    task = (
        f"Navigate to {url}\n\nComplete this task:\n{prompt}\n\n"
        "For mouse-only movement or hover, use move_mouse_to_text. Never synthesize "
        "DOM events or directly modify page state to claim completion. The action's "
        "parameters must be an object such as {\"text\": \"1\"}, never a bare string. "
        "For ordinary form interactions, use browser-use's native click action; "
        "For numbered signal-button tasks, call click_mouse_on_text exactly once for each label, "
        "in the requested order; a pending status is expected until the final label. "
        "Before calling done, use verify_page_status to provide read-only completion evidence. "
        "These custom mouse and verification tools are definitely bound to this agent; do not "
        "claim they are unavailable—call them using schema-shaped object parameters."
    )
    browser = make_browser(proxy_url)
    agent = None
    try:
        agent_kwargs = {
            "task": task,
            "llm": make_llm(provider, model, api_base, api_key, upstream_model),
            "tools": make_tools(),
            # The proxy can take longer than browser-use's 75-second default.
            # The parent process still enforces the total task timeout.
            "llm_timeout": float(os.environ.get("BROWSER_USE_LLM_TIMEOUT", "1200")),
        }
        if browser is not None:
            agent_kwargs["browser"] = browser
        agent = Agent(**agent_kwargs)
        # These benchmark tasks are short (normally 3-8 steps). Keep a finite
        # default so malformed structured responses cannot turn one sample
        # into a 100-step retry loop; callers can still override it explicitly.
        kwargs = {"max_steps": int(max_steps) if max_steps else 100}
        history = await agent.run(**kwargs)
        # Browser-use exposes an explicit task-level verdict.  A zero exit
        # code only means that its Python loop terminated, so preserve this
        # verdict for the orchestrator/manifest when available.
        task_success = None
        task_status = None
        checker = getattr(history, "is_successful", None)
        if callable(checker):
            try:
                verdict = checker()
            except Exception:
                verdict = None
            if isinstance(verdict, bool):
                task_success = verdict
                task_status = "completed" if verdict else "failed"
            elif verdict is None:
                # ``None`` means the framework is not done yet.  The runner
                # itself is nevertheless over, so classify this terminal
                # state as incomplete instead of a false success.
                task_success = False
                task_status = "incomplete"
        # Local benchmark pages expose an explicit completion contract. Some
        # OpenAI-compatible Gemini routes complete every browser action but
        # then wrap the final structured ``done`` response in markdown, so
        # Browser-use cannot parse it and reports an incomplete history. Read
        # the page contract without mutating it and preserve the real result.
        try:
            session = await agent.browser_session.get_or_create_cdp_session(focus=True)
            response = await session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": "document.body && document.body.dataset ? document.body.dataset.taskStatus : null",
                    "returnByValue": True,
                },
                session_id=session.session_id,
            )
            page_status = str(response.get("result", {}).get("value") or "").strip().lower()
        except Exception:
            page_status = ""
        if not page_status:
            page_status = str(verified_page_state.get("taskStatus") or "").strip().lower()
        if page_status in {"passed", "success", "succeeded", "completed", "successfully"}:
            task_success = True
            task_status = "completed"
        judgement = history.judgement() if hasattr(history, "judgement") else None
        judge_verdict = judgement.get("verdict") if isinstance(judgement, dict) else None
        # The local benchmark's explicit page contract is stronger evidence
        # than a vision judge guessing from an absent screenshot. Preserve a
        # concrete DOM pass; otherwise keep the judge as a useful safeguard
        # against an agent merely self-reporting success.
        page_passed = page_status in {"passed", "success", "succeeded", "completed", "successfully"}
        if isinstance(judge_verdict, bool) and not page_passed:
            task_success = judge_verdict
            task_status = "completed" if judge_verdict else "failed"
        if hasattr(history, "final_result"):
            try:
                output = history.final_result()
            except Exception:
                output = None
        else:
            output = str(history)
        try:
            json.dumps(output)
        except TypeError:
            output = str(output)
        result = {
            "url": url,
            "prompt": prompt,
            "output": output,
            "task_success": task_success,
            "task_status": task_status,
            "judgement": judgement,
        }
        (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        # A judge rejection is a task verdict, not a process failure. The
        # parent retains it alongside the independent completion evidence.
    finally:
        if agent is not None:
            # Browser-use may leave its internally-created browser alive until
            # process teardown, which is too abrupt for page lifecycle events.
            # Stop the in-page probe explicitly so its final in-memory batch is
            # queued through sendBeacon before the browser session is killed.
            try:
                session = await agent.browser_session.get_or_create_cdp_session(focus=True)
                await session.cdp_client.send.Runtime.evaluate(
                    params={
                        "expression": "window.__agentFingerprintMonitor && window.__agentFingerprintMonitor.stop()",
                        "returnByValue": True,
                    },
                    session_id=session.session_id,
                )
                await asyncio.sleep(0.1)
            except Exception:
                pass
            close = getattr(agent, "close", None)
            if callable(close):
                closed = close()
                if inspect.isawaitable(closed):
                    await closed
        elif browser is not None:
            close = getattr(browser, "close", None)
            if callable(close):
                closed = close()
                if inspect.isawaitable(closed):
                    await closed


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        raise
'''


class BrowserUseAdapter(BaseAgentAdapter):
    """Run Browser-use in its dedicated Conda environment."""

    adapter_name = "browseruse"

    def __init__(
        self,
        *,
        conda_env: str | None = "browser-use",
        conda_env_path: Path | str | None = None,
        llm_provider: str = "browser_use",
        llm_model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "RELAY_OPENAI_API_KEY",
        upstream_model: str | None = None,
        max_steps: int | None = None,
        disable_telemetry: bool = True,
        **kwargs,
    ) -> None:
        if max_steps is not None and max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if llm_provider not in {"browser_use", "openai", "anthropic"}:
            raise ValueError("llm_provider must be browser_use, openai, or anthropic")
        if conda_env_path is not None and conda_env == "browser-use":
            conda_env = None
        super().__init__(conda_env=conda_env, conda_env_path=conda_env_path, **kwargs)
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.api_base = api_base.rstrip("/") if api_base else None
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.upstream_model = upstream_model
        self.max_steps = max_steps
        self.disable_telemetry = disable_telemetry
        self.proxy_url: str | None = None

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        output_path = self.prepare_output_dir(output_dir)
        updates = {
            "AGENT_TASK_URL": url,
            "AGENT_TASK_PROMPT": prompt,
            "AGENT_OUTPUT_DIR": str(output_path),
            "BROWSER_USE_LLM_PROVIDER": self.llm_provider,
        }
        key = self.api_key
        if self.api_key:
            # Direct-provider mode (without api_base) reads the conventional
            # provider key from the environment.  Honour an explicitly
            # supplied key without putting it on the command line.
            updates[self.api_key_env] = self.api_key
        if self.llm_model:
            updates["BROWSER_USE_LLM_MODEL"] = self.llm_model
        if self.upstream_model:
            updates["BROWSER_USE_UPSTREAM_MODEL"] = self.upstream_model
        if self.api_base:
            updates["BROWSER_USE_API_BASE"] = self.api_base
            key = self.api_key or self.merged_environment().get(self.api_key_env)
            if key:
                updates["BROWSER_USE_API_KEY"] = key
        if self.max_steps is not None:
            updates["BROWSER_USE_MAX_STEPS"] = str(self.max_steps)
        if self.disable_telemetry:
            # Browser-use documents this environment switch for disabling
            # anonymized telemetry during controlled trace collection.
            updates["ANONYMIZED_TELEMETRY"] = "false"
        if self.proxy_url:
            # Browser-use 0.13 reads these variables into its BrowserProfile,
            # which is the reliable way to pass the proxy to Chromium itself;
            # generic HTTP_PROXY alone may affect Python clients but not the
            # launched browser process.
            updates["BROWSER_USE_PROXY_SERVER"] = self.proxy_url
            updates["BROWSER_USE_NO_PROXY"] = ""
        updates.update(self.proxy_environment(self.proxy_url))
        command = self.conda_command(["-"])
        return self.execute(
            command,
            output_dir=output_path,
            env=self.merged_environment(updates),
            input_text=_RUNNER,
            redact_values=(key,) if key else (),
        )
