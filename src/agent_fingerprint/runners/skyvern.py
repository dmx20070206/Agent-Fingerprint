
import asyncio, json, os, shutil, traceback
from pathlib import Path
from urllib.parse import unquote, urlsplit

async def main():
    from skyvern import Skyvern
    output_dir = Path(os.environ["AGENT_OUTPUT_DIR"])
    llm_api_base = os.environ.get("SKYVERN_LLM_API_BASE")
    if llm_api_base:
        from skyvern.schemas.llm import LLMConfig

        model = os.environ.get("SKYVERN_LLM_MODEL", "chat-gpt")
        # The provider prefix makes Skyvern/LiteLLM use the OpenAI-compatible
        # client.  LiteLLM strips it before sending the public alias to the
        # experiment gateway.
        llm_model = model if "/" in model else "openai/" + model
        protocol = os.environ.get("SKYVERN_LLM_PROTOCOL", "native").strip().lower()
        protocol_params = {}
        if protocol == "json":
            # Skyvern v2 prompts are JSON action protocol prompts, not OpenAI
            # function/tool prompts.  JSON object mode prevents an
            # OpenAI-compatible Gemini route from returning an empty assistant
            # message while leaving the other agents untouched.
            protocol_params["response_format"] = {"type": "json_object"}
        elif protocol not in {"text", "native"}:
            raise ValueError(
                "SKYVERN_LLM_PROTOCOL must be one of: json, text, native"
            )
        llm_config = LLMConfig(
            model_name=llm_model,
            required_env_vars=[],
            supports_vision=True,
            add_assistant_prefix=False,
            litellm_params={
                "api_key": os.environ.get("SKYVERN_LLM_API_KEY") or "sk-placeholder",
                "api_base": llm_api_base,
                "model_info": {"model_name": llm_model},
                **protocol_params,
            },
        )
        client = Skyvern.local(use_in_memory_db=True, llm_config=llm_config)
    else:
        kwargs = {"api_key": os.environ.get("SKYVERN_API_KEY", "")}
        if os.environ.get("SKYVERN_BASE_URL"):
            kwargs["base_url"] = os.environ["SKYVERN_BASE_URL"]
        client = Skyvern(**kwargs)
    call = {"prompt": os.environ["AGENT_TASK_PROMPT"], "url": os.environ["AGENT_TASK_URL"],
            "wait_for_completion": True}
    if os.environ.get("SKYVERN_MAX_STEPS"):
        call["max_steps"] = int(os.environ["SKYVERN_MAX_STEPS"])
    call["timeout"] = float(os.environ.get("SKYVERN_TIMEOUT", "1800"))
    try:
        result = await client.run_task(**call)
        def value(name, default=None):
            item = getattr(result, name, default)
            return item.value if hasattr(item, "value") else item

        # Embedded Skyvern stores artifacts below a temporary directory which
        # client.aclose() removes.  Copy every local artifact exposed by the
        # task result into the pipeline-owned run directory before closing the
        # client so recordings and screenshots survive the child process.
        artifact_copy_errors = []
        artifact_root = output_dir / "skyvern_artifacts"

        def persist_local_artifact(uri, category):
            if not isinstance(uri, str) or not uri.startswith("file://"):
                return uri
            parsed = urlsplit(uri)
            if parsed.netloc not in {"", "localhost"}:
                artifact_copy_errors.append(f"unsupported file URI host: {parsed.netloc}")
                return uri
            source = Path(unquote(parsed.path))
            if not source.is_file():
                artifact_copy_errors.append(f"artifact file does not exist: {source}")
                return uri
            destination_dir = artifact_root / category
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / source.name
            try:
                shutil.copy2(source, destination)
            except OSError as exc:
                artifact_copy_errors.append(f"failed to copy {source}: {exc}")
                return uri
            return destination.resolve().as_uri()

        recording_url = persist_local_artifact(value("recording_url"), "recordings")
        raw_screenshot_urls = value("screenshot_urls") or []
        if isinstance(raw_screenshot_urls, str):
            raw_screenshot_urls = [raw_screenshot_urls]
        screenshot_urls = [
            persist_local_artifact(uri, "screenshots") for uri in raw_screenshot_urls
        ]
        status = value("status")
        status_text = str(status).lower() if status is not None else ""
        success = status_text in {"completed", "succeeded", "success", "passed"}
        if not status_text:
            success = None
        payload = {"url": call["url"], "prompt": call["prompt"], "run_id": value("run_id"),
                   "status": status, "task_status": status, "task_success": success,
                   "output": value("output"), "failure_reason": value("failure_reason"),
                   "recording_url": recording_url, "screenshot_urls": screenshot_urls,
                   "artifact_copy_errors": artifact_copy_errors,
                   "step_count": value("step_count")}
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        (output_dir / "result.json").write_text(encoded, encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, default=str))
    finally:
        await client.aclose()

if __name__ == "__main__":
    try: asyncio.run(main())
    except Exception:
        traceback.print_exc()
        raise
