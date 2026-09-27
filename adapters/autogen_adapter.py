from __future__ import annotations
from pathlib import Path
from .base_adapter import AgentResult, BaseAgentAdapter

_RUNNER = r"""
import asyncio, json, os, re, traceback
from pathlib import Path

# Prefer MultimodalWebSurfer for vision-capable models; fall back to
# AssistantAgent for text-only models (e.g. deepseek-chat).
try:
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_ext.agents.web_surfer import MultimodalWebSurfer
    from autogen_agentchat.agents import AssistantAgent
    from autogen_core.models import AssistantMessage, UserMessage
    from autogen_core.tools import ParametersSchema, ToolSchema
    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from playwright.async_api import async_playwright
    _NEW_API = True
except ImportError:
    try:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.conditions import TextMentionTermination
        from autogen_agentchat.teams import RoundRobinGroupChat
        from autogen_core.models import AssistantMessage, UserMessage
        from autogen_ext.models.openai import OpenAIChatCompletionClient
        from playwright.async_api import async_playwright
        MultimodalWebSurfer = None
        _NEW_API = True
    except ImportError:
        import autogen
        _NEW_API = False

def _make_llm_config(api_base, api_key, model):
    cfg = {"model": model}
    if api_base:
        cfg["base_url"] = api_base
        cfg["api_key"] = api_key or "sk-placeholder"
    elif api_key:
        cfg["api_key"] = api_key
    return cfg

def _info_value(info, key, default=None):
    if isinstance(info, dict):
        return info.get(key, default)
    return getattr(info, key, default)

def _lookup_model_info(model):
    if not model:
        return None
    candidates = [model]
    if "/" in model:
        candidates.append(model.split("/", 1)[1])
    try:
        from autogen_ext.models.openai._model_info import get_info
        for candidate in candidates:
            try:
                return get_info(candidate)
            except (ValueError, KeyError):
                continue
    except ImportError:
        return None
    return None

def _infer_family(model):
    value = (model or "").lower().split("/", 1)[-1]
    if value.startswith("gpt-5"):
        # AutoGen 0.7.x defines GPT_5 but MultimodalWebSurfer's history
        # allow-list omits it.  GPT-4o is the closest supported compatibility
        # family for an OpenAI-compatible multimodal endpoint and preserves
        # prior turns/screenshots needed for browser workflows.
        return "gpt-4o"
    if value.startswith("gpt-4o"):
        return "gpt-4o"
    if value.startswith("gpt-4"):
        return "gpt-4"
    if value.startswith("o1"):
        return "o1"
    if value.startswith("o3"):
        return "o3"
    if value.startswith("o4"):
        return "o4"
    return "unknown"

def _is_gemini_model(model):
    value = (model or "").lower().split("/", 1)[-1]
    return value == "gemini" or value.startswith("gemini-")

def _gemini_compatible_messages(messages):
    normalized = list(messages)
    if not normalized or not isinstance(normalized[-1], AssistantMessage):
        return normalized

    # AutoGen 0.7 WebSurfer appends the current page observation before its
    # latest chat-history item.  From the second turn onward that item can be
    # an AssistantMessage, which leaves the request ending in a model turn.
    # Gemini's OpenAI-compatible endpoint rejects that shape.  Put the current
    # browser observation back after the prior model turn so it remains the
    # user input Gemini should act on.
    if len(normalized) >= 2 and isinstance(normalized[-2], UserMessage):
        normalized[-2], normalized[-1] = normalized[-1], normalized[-2]
    else:
        normalized.append(UserMessage(
            content="Continue the task using the latest available observation.",
            source="gemini_compat",
        ))
    return normalized

if _NEW_API:
    class GeminiCompatibleOpenAIChatCompletionClient(OpenAIChatCompletionClient):
        async def create(self, messages, **kwargs):
            return await super().create(_gemini_compatible_messages(messages), **kwargs)

    class GatewayCompatibleOpenAIChatCompletionClient(OpenAIChatCompletionClient):
        # OpenAI-compatible relays can also reject assistant-prefill requests.
        # Keep the latest browser observation after the prior assistant turn.
        async def create(self, messages, **kwargs):
            return await super().create(_gemini_compatible_messages(messages), **kwargs)
else:
    GeminiCompatibleOpenAIChatCompletionClient = None

def _make_client(cfg, vision=None, capability_model=None):
    client_kwargs = dict(cfg)
    # The OpenAI SDK's default retry backoff can sleep for roughly a minute
    # after each failed upstream request, making a task appear hung.  One
    # attempt is preferable for the orchestrator, which can record the error
    # and apply its own wall-clock policy.  Operators may opt back in through
    # AUTOGEN_MAX_RETRIES.
    try:
        client_kwargs.setdefault("max_retries", int(os.environ.get("AUTOGEN_MAX_RETRIES", "0")))
    except ValueError:
        raise ValueError("AUTOGEN_MAX_RETRIES must be an integer")
    info = _lookup_model_info(capability_model or client_kwargs["model"])
    if info is not None:
        model_vision = bool(_info_value(info, "vision", False))
        from autogen_core.models import ModelInfo
        client_kwargs["model_info"] = ModelInfo(
            vision=model_vision if vision is None else vision,
            function_calling=bool(_info_value(info, "function_calling", True)),
            json_output=bool(_info_value(info, "json_output", True)),
            structured_output=bool(_info_value(info, "structured_output", False)),
            family=_info_value(info, "family", _infer_family(capability_model or client_kwargs["model"])),
            multiple_system_messages=bool(_info_value(info, "multiple_system_messages", True)),
        )
    else:
        from autogen_core.models import ModelInfo
        effective_vision = vision if vision is not None else False
        client_kwargs.setdefault("model_info", ModelInfo(
            vision=effective_vision, function_calling=True, json_output=True,
            structured_output=False,
            family=_infer_family(capability_model or client_kwargs["model"]),
        ))
    client_class = (
        GeminiCompatibleOpenAIChatCompletionClient
        if _is_gemini_model(capability_model or client_kwargs["model"])
        else OpenAIChatCompletionClient
    )
    if client_class is OpenAIChatCompletionClient and client_kwargs.get("base_url"):
        client_class = GatewayCompatibleOpenAIChatCompletionClient
    return client_class(**client_kwargs)

def _parse_optional_bool(value):
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid AUTOGEN_VISION value: {value!r}")

def _model_supports_vision(cfg, capability_model=None, explicit_vision=None):
    if explicit_vision is not None:
        return explicit_vision
    model = capability_model or cfg["model"]
    # LiteLLM model names include a provider prefix, while AutoGen's model
    # registry generally expects the provider-native name.
    info = _lookup_model_info(model)
    if info is not None:
        return bool(_info_value(info, "vision", False))
    # Private/new OpenAI-compatible models may not yet exist in AutoGen's
    # registry.  LiteLLM's provider prefix still gives a useful conservative
    # fallback.  Other unknown providers remain text-only unless overridden.
    return model.startswith("openai/")

_SUCCESS_KEYWORDS = (
    "task complete", "通过", "task_complete", "successfully booked",
    "successfully been booked", "booking confirmed", "thank you for booking",
    "flight has been booked", "通过：转录完全一致", "flight has successfully been booked",
)

def _content_text(content):
    # Return textual parts from both TextMessage and MultiModalMessage content.
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(value for value in content if isinstance(value, str))
    return ""

def _is_success_text(text):
    normalized = text.lower()
    return any(keyword in normalized for keyword in _SUCCESS_KEYWORDS)

if MultimodalWebSurfer is not None:
    _SELECT_OPTION_TOOL = ToolSchema(
        name="select_option",
        description="Select an option in a native HTML select or combobox control.",
        parameters=ParametersSchema(
            type="object",
            properties={
                "reasoning": {
                    "type": "string",
                    "description": "A short description of the action and reason for it.",
                },
                "select_field_id": {
                    "type": "integer",
                    "description": "The numeric id of the select or combobox control.",
                },
                "option": {
                    "type": "string",
                    "description": "The visible label or value of the option to select.",
                },
            },
            required=["reasoning", "select_field_id", "option"],
        ),
    )

    class FormWebSurfer(MultimodalWebSurfer):
        # AutoGen 0.7's stock surfer can click a native <select>, but it has no
        # operation that chooses one of its options.  Form workflows otherwise
        # loop on the open control until their turn/time budget is exhausted.
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.default_tools.append(_SELECT_OPTION_TOOL)

        def _format_target_list(self, ids, rects):
            targets = super()._format_target_list(ids, rects)
            return [
                re.sub(
                    r'"role":\s*"combobox",\s*"tools":\s*\["click",\s*"hover"\]',
                    '"role": "combobox", "tools": ["select_option"]',
                    target,
                )
                for target in targets
            ]

        async def _execute_tool(self, message, rects, tool_names, cancellation_token=None):
            if not message or message[0].name != "select_option":
                return await super()._execute_tool(
                    message, rects, tool_names, cancellation_token=cancellation_token
                )

            args = json.loads(message[0].arguments)
            target_id = str(args.get("select_field_id"))
            requested = str(args.get("option", "")).strip()
            if not requested:
                raise ValueError("select_option requires a non-empty option")
            assert self._page is not None
            target = self._page.locator(f"[__elementId='{target_id}']")
            await target.wait_for(timeout=5000)
            tag_name = await target.evaluate("element => element.tagName.toLowerCase()")
            if tag_name != "select":
                raise ValueError("select_option target is not a native select control")

            # Resolve labels and values case-insensitively, then let Playwright
            # perform the selection so the page receives normal input/change
            # events and can enable its Next button.
            option_value = await target.evaluate(
                "(element, requested) => {"
                " const wanted = requested.trim().toLocaleLowerCase();"
                " const match = Array.from(element.options).find(option =>"
                " option.text.trim().toLocaleLowerCase() === wanted ||"
                " option.value.trim().toLocaleLowerCase() === wanted);"
                " return match ? match.value : null;"
                " }",
                requested,
            )
            if option_value is None:
                raise ValueError(f"No option matching {requested!r} exists")
            await target.select_option(value=option_value)
            await self._playwright_controller.sleep(self._page, 1)
            target_name = self._target_name(target_id, rects) or "combobox"
            return f"I selected '{requested}' in '{target_name}'."

else:
    FormWebSurfer = None

async def _run_web_surfer(task, cfg, max_turns, debug_dir, headless, capability_model=None):
    client = _make_client(cfg, vision=True, capability_model=capability_model)
    agent = FormWebSurfer(
        "web_surfer",
        model_client=client,
        headless=headless,
        debug_dir=str(debug_dir) if debug_dir else None,
        to_save_screenshots=bool(debug_dir),
        start_page=None,
    )
    # A message-based limit counts the surfer's inner tool call and multimodal
    # observation separately.  ``max_turns`` counts actual agent turns, which
    # is the contract exposed by AutoGenAdapter.
    completion_term = (
        TextMentionTermination("TASK_COMPLETE", sources=[agent.name])
        | TextMentionTermination("successfully booked", sources=[agent.name])
    )
    team = RoundRobinGroupChat(
        [agent], termination_condition=completion_term, max_turns=max_turns
    )
    agent_lines = []
    trace = []
    inner_cursor = 0
    try:
        async for msg in team.run_stream(task=task):
            content = getattr(msg, "content", None)
            item = {"message_type": type(msg).__name__, "content_type": type(content).__name__}
            if isinstance(content, str):
                item["text_length"] = len(content)
            elif isinstance(content, list):
                item["items"] = [
                    {"type": type(value).__name__, "name": getattr(value, "name", None)}
                    for value in content
                ]
            trace.append(item)
            # MultimodalWebSurfer exposes the actual browser function call in
            # ``inner_messages`` before yielding its screenshot observation.
            # Keep only the function name and argument length so diagnostics
            # cannot duplicate task secrets or image data.
            inner_messages = getattr(agent, "inner_messages", [])
            for inner in inner_messages[inner_cursor:]:
                inner_content = getattr(inner, "content", None)
                if isinstance(inner_content, str):
                    action = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", inner_content)
                    if action:
                        trace.append({
                            "message_type": type(inner).__name__,
                            "action": action.group(1),
                            "argument_length": len(inner_content),
                        })
            inner_cursor = len(inner_messages)
            text_content = _content_text(content)
            # Exclude the initial user task echo.  In particular, completion
            # phrases in the prompt must not count as evidence of success.
            if text_content and getattr(msg, "source", None) == agent.name:
                if text_content.startswith("Web surfing error:"):
                    raise RuntimeError(text_content)
                agent_lines.append(text_content)
    finally:
        await agent.close()
    result_text = agent_lines[-1] if agent_lines else None
    task_success = any(_is_success_text(line) for line in agent_lines)
    return result_text, task_success, "completed" if task_success else "failed", trace

class TextBrowser:
    '''A Playwright browser exposed to a text-only model through DOM tools.'''

    def __init__(self, headless=True):
        self.headless = headless
        self._playwright = None
        self._browser = None
        self.page = None

    async def start(self, url):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self.page = await self._browser.new_page()
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(300)

    async def close(self):
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def observe(self):
        if self.page is None:
            raise RuntimeError("browser has not been started")
        snapshot = await self.page.evaluate(r'''
() => {
  window.__autogenTextBrowserId = window.__autogenTextBrowserId || 1;
  const selector = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[contenteditable="true"]', '[id]', '[data-step]',
    '[role="button"]', '[role="link"]', '[role="checkbox"]',
    '[role="radio"]', '[role="combobox"]', '[role="textbox"]',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' &&
      rect.width > 0 && rect.height > 0;
  };
  const clean = (value, limit = 180) => String(value || '')
    .replace(/\s+/g, ' ').trim().slice(0, limit);
  const elements = [];
  for (const element of document.querySelectorAll(selector)) {
    if (!visible(element)) continue;
    let id = element.getAttribute('data-autogen-text-id');
    if (!id) {
      id = String(window.__autogenTextBrowserId++);
      element.setAttribute('data-autogen-text-id', id);
    }
    const tag = element.tagName.toLowerCase();
    const item = {
      id: Number(id), tag,
      role: clean(element.getAttribute('role')),
      type: clean(element.getAttribute('type')),
      name: clean(element.getAttribute('name')),
      text: clean(element.innerText || element.textContent),
      label: clean(element.getAttribute('aria-label')),
      placeholder: clean(element.getAttribute('placeholder')),
      value: clean(element.value),
      checked: Boolean(element.checked),
      disabled: Boolean(element.disabled)
    };
    if (tag === 'select') {
      item.options = Array.from(element.options).map(option => ({
        label: clean(option.text), value: clean(option.value),
        selected: option.selected
      })).slice(0, 100);
    }
    elements.push(item);
    if (elements.length >= 250) break;
  }
  return {
    title: document.title,
    url: location.href,
    viewport: {scrollY: Math.round(scrollY), height: innerHeight,
      documentHeight: document.documentElement.scrollHeight},
    text: clean(document.body ? document.body.innerText : '', 12000),
    elements
  };
}
''')
        return json.dumps(snapshot, ensure_ascii=False)

    def _target(self, element_id):
        if self.page is None:
            raise RuntimeError("browser has not been started")
        return self.page.locator(f'[data-autogen-text-id="{int(element_id)}"]').first

    async def click(self, element_id):
        await self._target(element_id).click(timeout=5000)
        await self.page.wait_for_timeout(300)
        return await self.observe()

    async def type_text(self, element_id, text, clear=True):
        target = self._target(element_id)
        if clear:
            await target.fill(str(text), timeout=5000)
        else:
            await target.press_sequentially(str(text), delay=20, timeout=5000)
        return await self.observe()

    async def select(self, element_id, option):
        target = self._target(element_id)
        requested = str(option)
        try:
            await target.select_option(label=requested, timeout=5000)
        except Exception:
            await target.select_option(value=requested, timeout=5000)
        await self.page.wait_for_timeout(300)
        return await self.observe()

    async def hover(self, element_id):
        await self._target(element_id).hover(timeout=5000)
        await self.page.wait_for_timeout(300)
        return await self.observe()

    async def scroll(self, direction, amount):
        delta = max(1, min(int(amount), 5000))
        if str(direction).lower() in {"up", "left"}:
            delta = -delta
        if str(direction).lower() in {"left", "right"}:
            await self.page.mouse.wheel(delta, 0)
        else:
            await self.page.mouse.wheel(0, delta)
        await self.page.wait_for_timeout(300)
        return await self.observe()

    async def press(self, key, element_id=0):
        if int(element_id):
            await self._target(element_id).press(str(key), timeout=5000)
        else:
            await self.page.keyboard.press(str(key))
        await self.page.wait_for_timeout(300)
        return await self.observe()

    async def back(self):
        await self.page.go_back(wait_until="domcontentloaded")
        await self.page.wait_for_timeout(300)
        return await self.observe()

    async def wait(self, seconds):
        delay = max(0.0, min(float(seconds), 10.0))
        await self.page.wait_for_timeout(int(delay * 1000))
        return await self.observe()

async def _run_text_browser(task, url, cfg, max_turns, headless, capability_model=None):
    browser = TextBrowser(headless=headless)
    trace = []

    async def observe() -> str:
        '''Read the current page's visible text and numbered interactive elements.'''
        trace.append({"action": "observe"})
        return await browser.observe()

    async def click(element_id: int) -> str:
        '''Click the interactive element with the given numeric id.'''
        trace.append({"action": "click", "element_id": element_id})
        return await browser.click(element_id)

    async def type_text(element_id: int, text: str, clear: bool = True) -> str:
        '''Enter text into an input or textarea; clear its old value by default.'''
        trace.append({"action": "type_text", "element_id": element_id})
        return await browser.type_text(element_id, text, clear)

    async def select_option(element_id: int, option: str) -> str:
        '''Select an HTML select option by its visible label or value.'''
        trace.append({"action": "select_option", "element_id": element_id})
        return await browser.select(element_id, option)

    async def hover(element_id: int) -> str:
        '''Move the mouse over the interactive element with the given id.'''
        trace.append({"action": "hover", "element_id": element_id})
        return await browser.hover(element_id)

    async def scroll(direction: str = "down", amount: int = 700) -> str:
        '''Scroll the page up, down, left, or right by an amount in pixels.'''
        trace.append({"action": "scroll", "direction": direction, "amount": amount})
        return await browser.scroll(direction, amount)

    async def press(key: str, element_id: int = 0) -> str:
        '''Press a keyboard key globally, or on an element when element_id is nonzero.'''
        trace.append({"action": "press", "key": key, "element_id": element_id})
        return await browser.press(key, element_id)

    async def go_back() -> str:
        '''Navigate back one entry in browser history.'''
        trace.append({"action": "go_back"})
        return await browser.back()

    async def wait(seconds: float = 1.0) -> str:
        '''Wait up to ten seconds for the page to update, then observe it.'''
        trace.append({"action": "wait", "seconds": seconds})
        return await browser.wait(seconds)

    client = _make_client(cfg, vision=False, capability_model=capability_model)
    system_message = (
        "You are a browser automation agent. The browser is already open at the task URL. "
        "Use the supplied tools to inspect and operate it. Tool results contain visible page "
        "text plus numbered interactive elements. Element ids can change after navigation, so "
        "use ids from the newest observation. Never claim success without observing the requested "
        "final state. When the task is truly complete, reply with TASK_COMPLETE and a short summary."
    )
    try:
        await browser.start(url)
        agent = AssistantAgent(
            "text_web_surfer", model_client=client,
            tools=[observe, click, type_text, select_option, hover, scroll, press, go_back, wait],
            system_message=system_message,
            reflect_on_tool_use=False,
            max_tool_iterations=max(1, max_turns),
        )
    except Exception:
        await browser.close()
        await client.close()
        raise
    term = TextMentionTermination("TASK_COMPLETE", sources=[agent.name])
    team = RoundRobinGroupChat([agent], termination_condition=term, max_turns=max_turns)
    agent_lines = []
    try:
        initial_observation = await browser.observe()
        enriched_task = f"{task}\n\nInitial browser observation:\n{initial_observation}"
        async for msg in team.run_stream(task=enriched_task):
            content = getattr(msg, "content", None)
            text_content = _content_text(content)
            if text_content and getattr(msg, "source", None) == agent.name:
                agent_lines.append(text_content)
    finally:
        await browser.close()
        await client.close()
    result_text = agent_lines[-1] if agent_lines else None
    task_success = any(_is_success_text(line) for line in agent_lines)
    return result_text, task_success, "completed" if task_success else "failed", trace

def _run_legacy_api(task, cfg, max_turns):
    sys_msg = ("You are a helpful web research assistant. Complete the given task. "
               "When finished, reply with TASK_COMPLETE.")
    assistant = autogen.AssistantAgent("assistant", llm_config={"config_list": [cfg]},
                                       system_message=sys_msg)
    proxy = autogen.UserProxyAgent("user_proxy", human_input_mode="NEVER",
                                   max_consecutive_auto_reply=max_turns,
                                   is_termination_msg=lambda m: "TASK_COMPLETE" in (m.get("content") or ""),
                                   code_execution_config=False)
    chat = proxy.initiate_chat(assistant, message=task)
    last = None
    if hasattr(chat, "chat_history") and chat.chat_history:
        last = chat.chat_history[-1].get("content")
    elif hasattr(chat, "summary"):
        last = chat.summary
    ok = "TASK_COMPLETE" in (last or "")
    return last, ok, "completed" if ok else "failed"

async def main():
    url = os.environ["AGENT_TASK_URL"]
    prompt = os.environ["AGENT_TASK_PROMPT"]
    output_dir = Path(os.environ["AGENT_OUTPUT_DIR"])
    model = os.environ.get("AUTOGEN_LLM_MODEL", "gpt-4o")
    capability_model = os.environ.get("AUTOGEN_UPSTREAM_MODEL") or None
    explicit_vision = _parse_optional_bool(os.environ.get("AUTOGEN_VISION"))
    api_base = os.environ.get("AUTOGEN_API_BASE") or None
    api_key = os.environ.get("AUTOGEN_API_KEY") or None
    max_turns = int(os.environ.get("AUTOGEN_MAX_TURNS", "100"))
    headless = os.environ.get("AUTOGEN_HEADLESS", "1") != "0"
    debug_dir = output_dir / "screenshots" if os.environ.get("AUTOGEN_SCREENSHOTS") else None
    task = f"Navigate to {url} and complete the following task:\n{prompt}"
    cfg = _make_llm_config(api_base, api_key, model)
    result_text = task_success = task_status = None
    trace = None
    try:
        if (_NEW_API and MultimodalWebSurfer is not None
                and _model_supports_vision(cfg, capability_model, explicit_vision)):
            result_text, task_success, task_status, trace = await _run_web_surfer(
                task, cfg, max_turns, debug_dir, headless, capability_model)
        elif _NEW_API:
            result_text, task_success, task_status, trace = await _run_text_browser(
                task, url, cfg, max_turns, headless, capability_model)
        else:
            result_text, task_success, task_status = _run_legacy_api(task, cfg, max_turns)
    except Exception:
        traceback.print_exc()
        task_success, task_status = False, "error"
    result = {"url": url, "prompt": prompt, "output": result_text,
              "task_success": task_success, "task_status": task_status}
    if os.environ.get("AUTOGEN_TRACE") and trace is not None:
        result["trace"] = trace
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if task_success is False:
        raise SystemExit(3)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        raise
"""


class AutoGenAdapter(BaseAgentAdapter):
    """Run an AutoGen agent in its dedicated Conda environment."""

    adapter_name = "autogen"

    def __init__(
        self,
        *,
        conda_env=None,
        conda_env_path=None,
        llm_model="gpt-4o",
        upstream_model=None,
        vision=None,
        api_base=None,
        api_key=None,
        api_key_env="RELAY_OPENAI_API_KEY",
        max_turns=100,
        headless=True,
        save_screenshots=False,
        trace=False,
        **kwargs,
    ):
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        if vision is not None and not isinstance(vision, bool):
            raise ValueError("vision must be true, false, or None")
        if conda_env is None and conda_env_path is None:
            conda_env = "autogen"
        super().__init__(conda_env=conda_env, conda_env_path=conda_env_path, **kwargs)
        self.llm_model = llm_model
        self.upstream_model = upstream_model
        self.vision = vision
        self.api_base = api_base.rstrip("/") if api_base else None
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.max_turns = max_turns
        self.headless = headless
        self.save_screenshots = save_screenshots
        self.trace = trace

    def run_task(self, url, prompt, output_dir):
        self.validate_task(url, prompt)
        output_path = self.prepare_output_dir(output_dir)
        updates = {
            "AGENT_TASK_URL": url,
            "AGENT_TASK_PROMPT": prompt,
            "AGENT_OUTPUT_DIR": str(output_path),
            "AUTOGEN_LLM_MODEL": self.llm_model,
            "AUTOGEN_MAX_TURNS": str(self.max_turns),
            "AUTOGEN_HEADLESS": "0" if not self.headless else "1",
            "AUTOGEN_SCREENSHOTS": "1" if self.save_screenshots else "",
            "AUTOGEN_TRACE": "1" if self.trace else "",
        }
        if self.upstream_model:
            updates["AUTOGEN_UPSTREAM_MODEL"] = self.upstream_model
        if self.vision is not None:
            updates["AUTOGEN_VISION"] = "1" if self.vision else "0"
        if self.api_base:
            updates["AUTOGEN_API_BASE"] = self.api_base
            key = self.api_key or self.merged_environment().get(self.api_key_env)
            if key:
                updates["AUTOGEN_API_KEY"] = key
        elif self.api_key:
            updates[self.api_key_env] = self.api_key
        return self.execute(
            self.conda_command(["-c", _RUNNER]),
            output_dir=output_path,
            env=self.merged_environment(updates),
            redact_values=(self.api_key,) if self.api_key else (),
        )
