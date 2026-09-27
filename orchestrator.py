"""Command-line entry point for the complete Agent-Fingerprint pipeline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from dotenv import load_dotenv

from gateway import GatewayConfig, LiteLLMGateway
from gateway.mock_openai import MockOpenAIGateway
from pipeline import CycleResult, PipelineRunner

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_GATEWAY_CONFIG = PROJECT_ROOT / "config" / "litellm.config.yaml"
DEFAULT_RUNTIME_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
SUPPORTED_AGENTS = ("browseruse", "webvoyager", "skyvern", "autogen", "agente", "mock", "manual")


def clean_local_agent_state() -> None:
    """Remove stale browser profiles created by this repository's adapters."""
    temp_root = PROJECT_ROOT / "temp"
    if not temp_root.is_dir():
        return
    for path in temp_root.glob("skyvern_browser_*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

# Load local credentials automatically when present.  ``override=False`` keeps
# explicitly supplied environment variables authoritative.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one or more Web Agent collection cycles (sandbox + gateway + probes)"
    )
    parser.add_argument("--agent", choices=SUPPORTED_AGENTS, default="webvoyager")
    parser.add_argument(
        "--manual",
        dest="agent",
        action="store_const",
        const="manual",
        help="Collect a fingerprint through human browser interaction",
    )
    parser.add_argument("--agents", help="Comma-separated agents for a task matrix; overrides --agent")
    parser.add_argument("--task-file", type=Path, help="JSON/JSONL tasks with url/prompt (or web/ques) fields")
    parser.add_argument("--url", help="Run one task directly; relative paths are served by the local sandbox")
    parser.add_argument("--prompt", help="Task prompt for --url (optional in manual mode)")
    parser.add_argument("--output-dir", type=Path, help="Exact output directory for one task")
    parser.add_argument("--output-root", type=Path, default=Path("data") / "runs")
    parser.add_argument("--run-id", help="Stable id for a single run (safe filename characters only)")
    parser.add_argument("--overwrite", action="store_true", help="Allow reusing a non-empty --output-dir")
    parser.add_argument("--config", type=Path, default=DEFAULT_RUNTIME_CONFIG, help="Runtime configuration YAML")

    parser.add_argument("--model", help="Public model alias sent to the Agent")
    parser.add_argument("--upstream-model", help="Provider model used by LiteLLM for this run")
    parser.add_argument("--route-alias", help="Alias to rewrite (defaults to --model or chat-gpt)")
    parser.add_argument(
        "--provider-key-env",
        help="Provider credential environment variable for --upstream-model; existing route value is preserved when omitted",
    )
    parser.add_argument("--gateway-config", type=Path, default=DEFAULT_GATEWAY_CONFIG)
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=4000)
    parser.add_argument("--gateway-executable", default="litellm")
    parser.add_argument(
        "--gateway-startup-timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for LiteLLM to become ready (newer releases can take longer to import)",
    )
    parser.add_argument("--master-key-env", default="LITELLM_MASTER_KEY")
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Use the local deterministic OpenAI-compatible endpoint (no API key; for smoke tests)",
    )
    parser.add_argument("--mock-delay-ms", type=float, default=10.0)
    parser.add_argument("--mock-response", default="DONE")
    parser.add_argument(
        "--no-gateway", action="store_true",
        help="Do not start LiteLLM (useful when the Agent service owns its LLM, such as Skyvern)",
    )

    parser.add_argument("--sandbox-directory", type=Path)
    parser.add_argument("--no-sandbox", action="store_true", help="Do not start the built-in HTTP sandbox")
    parser.add_argument("--no-monitor", action="store_true", help="Serve HTML without injecting inject_monitor.js")
    parser.add_argument(
        "--interaction-delay",
        type=float,
        default=0.0,
        help="Seconds to block interaction after each monitored page opens (default: %(default)s)",
    )
    parser.add_argument(
        "--trace-finalize-timeout",
        type=float,
        default=8.0,
        help="Seconds to wait for the browser's final acknowledged trace upload (default: %(default)s)",
    )
    parser.add_argument("--no-network-probe", action="store_true", help="Skip tcpdump")
    for level in range(1, 5):
        dest = f"collect_l{level}"
        parser.add_argument(
            f"--collect-l{level}", dest=dest, nargs="?", const=True, default=True, type=_parse_bool,
            help=f"Collect L{level} fingerprint (default: enabled; accepts true/false)",
        )
        parser.add_argument(f"--no-collect-l{level}", dest=dest, action="store_false")
    parser.add_argument("--network-interface", default="any")
    parser.add_argument(
        "--capture-filter",
        help="Optional tcpdump BPF filter (for example: 'host 127.0.0.1 and port 4000')",
    )
    parser.add_argument("--sniffer-executable")
    parser.add_argument("--capture-startup-timeout", type=float, default=10.0)

    parser.add_argument("--timeout", type=float, help="Agent wall-clock timeout in seconds")
    parser.add_argument("--max-iter", type=int, default=100, help="WebVoyager iteration limit")
    parser.add_argument(
        "--max-steps", type=int,
        default=100,
        help="Framework step/turn limit for Browser-use, Skyvern, AutoGen, and Agent-E",
    )
    parser.add_argument(
        "--autogen-vision", type=_parse_bool,
        help="Explicitly enable/disable AutoGen's browser surfer; otherwise infer it from the LiteLLM upstream model",
    )
    parser.add_argument(
        "--autogen-screenshots", action="store_true",
        help="Save AutoGen's per-turn browser screenshots under the run directory",
    )
    parser.add_argument(
        "--autogen-trace", action="store_true",
        help="Record redacted AutoGen response-structure metadata in result.json",
    )
    parser.add_argument("--skyvern-api-key", help="Skyvern API key (defaults to SKYVERN_API_KEY)")
    parser.add_argument("--skyvern-base-url", help="Skyvern API base URL for self-hosted deployments")
    parser.add_argument(
        "--agente-endpoint",
        default=os.environ.get("AGENTE_ENDPOINT"),
        help="Agent-E /execute_task URL (defaults to AGENTE_ENDPOINT)",
    )
    parser.add_argument("--headed", action="store_true", help="Show the WebVoyager browser")
    parser.add_argument(
        "--manual-browser",
        help="Browser registered with Python's webbrowser module (for example: firefox or chromium)",
    )
    parser.add_argument(
        "--manual-no-open",
        action="store_true",
        help="In manual mode, print the local URL without opening a browser automatically",
    )
    parser.add_argument(
        "--manual-network-probe",
        action="store_true",
        help="Also run tcpdump during manual mode (disabled by default for desktop use)",
    )
    return parser


def _parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"task file does not exist: {path}")
    if path.suffix.lower() == ".jsonl":
        tasks = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                tasks.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        tasks = value.get("tasks", []) if isinstance(value, dict) and "tasks" in value else value
        if isinstance(tasks, dict):
            tasks = [tasks]
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task file must contain a non-empty JSON array/object or JSONL records")
    normalized: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, 1):
        if not isinstance(task, Mapping):
            raise ValueError(f"task {index} must be a JSON object")
        url = task.get("url", task.get("web"))
        prompt = task.get("prompt", task.get("ques"))
        if url is None or prompt is None:
            raise ValueError(f"task {index} requires url/prompt (or web/ques)")
        item = dict(task)
        item["url"] = str(url)
        item["prompt"] = str(prompt)
        normalized.append(item)
    return normalized


def _apply_cli_defaults(tasks: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Apply command-line routing defaults without mutating loaded task data."""

    defaults = {
        "model": args.model,
        "upstream_model": args.upstream_model,
        "provider_key_env": args.provider_key_env,
        "route_alias": args.route_alias,
        "agent_timeout": args.timeout,
    }
    return [{**task, **{key: task.get(key, value) for key, value in defaults.items()}} for task in tasks]


def _build_gateway_factory(args: argparse.Namespace):
    selected_agents = [
        item.strip() for item in (args.agents.split(",") if args.agents else [args.agent]) if item.strip()
    ]
    if args.no_gateway or (selected_agents and set(selected_agents) == {"manual"}):
        return None
    # 本地 Mock 模式 (假 LLM, 返回固定内容)
    if args.mock_llm:

        def make_mock(run_dir: Path) -> MockOpenAIGateway:
            return MockOpenAIGateway(
                run_dir / "logs" / "mock_gateway",
                host=args.gateway_host,
                port=0,
                delay_seconds=args.mock_delay_ms / 1000,
                response_text=args.mock_response,
            )

        return make_mock

    # 真实 LiteLLM 模式
    # 读取主密钥，用于 LiteLLM Proxy 的认证
    master_key = os.environ.get(args.master_key_env)
    # 主密钥必须存在 & 有 sk- 前缀
    if not master_key:
        raise RuntimeError(
            f"{args.master_key_env} is not set; export it before starting LiteLLM "
            "(or pass --mock-llm for an offline smoke test)"
        )
    if not master_key.startswith("sk-"):
        raise RuntimeError(f"{args.master_key_env} must start with 'sk-' for LiteLLM proxy authentication")

    # 读取 litellm.config.yaml
    source = GatewayConfig.load(args.gateway_config)

    def make_litellm(run_dir: Path) -> LiteLLMGateway:
        # 复制一份配置：config/litellm.config.yaml -> 当前任务目录/litellm.config.yaml
        isolated = GatewayConfig(run_dir / "logs" / "litellm.config.yaml", source.document())
        return LiteLLMGateway(
            isolated,  # 专属 LiteLLM 配置
            host=args.gateway_host,  # 网关监听地址
            port=args.gateway_port,  # 网关端口
            executable=args.gateway_executable,  # LiteLLM 可执行程序
            master_key_env=args.master_key_env,  # 主密钥所在环境变量名
            startup_timeout=args.gateway_startup_timeout,
            output_dir=run_dir / "logs",
        )

    return make_litellm


def _build_runner(args: argparse.Namespace) -> PipelineRunner:
    gateway_factory = _build_gateway_factory(args)
    # 每种 Agent 的配置
    adapter_options: dict[str, dict[str, Any]] = {}
    # 从配置文件中读取各个 Agent 需要启动的 Conda 环境名
    runtime_config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    conda = runtime_config.get("conda", {})
    # 将 Conda 环境名写入 adapter_options
    for name in ("webvoyager", "browseruse", "skyvern", "autogen", "agente"):
        config_name = "agent-e" if name == "agente" else name
        if conda.get(config_name):
            value = str(conda[config_name])
            if Path(value).is_absolute():
                raise ValueError(f"conda environment for {name} must be a name, not an absolute path")
            adapter_options[name] = {"conda_env": value}
    # 定制化 webvoyager 和 browseruse 的配置
    adapter_options.setdefault("webvoyager", {}).update({"max_iter": args.max_iter, "headless": not args.headed})
    if args.max_steps is not None:
        adapter_options.setdefault("browseruse", {})["max_steps"] = args.max_steps
        adapter_options.setdefault("skyvern", {})["max_steps"] = args.max_steps
        adapter_options.setdefault("autogen", {})["max_turns"] = args.max_steps
        adapter_options.setdefault("agente", {})["max_steps"] = args.max_steps
    if args.autogen_vision is not None:
        adapter_options.setdefault("autogen", {})["vision"] = args.autogen_vision
    if args.autogen_screenshots:
        adapter_options.setdefault("autogen", {})["save_screenshots"] = True
    if args.autogen_trace:
        adapter_options.setdefault("autogen", {})["trace"] = True
    if args.skyvern_api_key:
        adapter_options.setdefault("skyvern", {})["api_key"] = args.skyvern_api_key
    if args.skyvern_base_url:
        adapter_options.setdefault("skyvern", {})["base_url"] = args.skyvern_base_url
    if args.agente_endpoint:
        adapter_options.setdefault("agente", {})["endpoint_url"] = args.agente_endpoint
    adapter_options.setdefault("manual", {}).update(
        {"browser": args.manual_browser, "open_browser": not args.manual_no_open}
    )

    def make_capture(network_dir: Path):
        from probes.traffic_sniffer import TrafficSniffer

        return TrafficSniffer(
            network_dir,
            backend="tcpdump",
            interface=args.network_interface,
            capture_filter=args.capture_filter,
            executable=args.sniffer_executable,
            startup_timeout=args.capture_startup_timeout,
        )

    manual_only = bool(
        {
            item.strip()
            for item in (args.agents.split(",") if args.agents else [args.agent])
            if item.strip()
        }
        == {"manual"}
    )
    use_network_probe = not args.no_network_probe and (not manual_only or args.manual_network_probe)
    return PipelineRunner(
        output_root=args.output_root,
        sandbox_directory=args.sandbox_directory,
        start_sandbox=not args.no_sandbox,
        inject_monitor=not args.no_monitor,
        gateway_factory=gateway_factory,
        sniffer_factory=None if not use_network_probe else make_capture,
        adapter_options=adapter_options,
        use_network_probe=use_network_probe,
        collect_l1=args.collect_l1,
        collect_l2=args.collect_l2,
        collect_l3=args.collect_l3,
        collect_l4=args.collect_l4,
        interaction_delay_seconds=args.interaction_delay,
        # The browser remains open when a manual session ends.  Its last
        # scroll event can spend 100 ms in the UI throttle and another 250 ms
        # in the upload batch, so retain the collector long enough to receive
        # that final batch after the operator presses Enter.
        trace_grace_seconds=0.5 if manual_only else 0.15,
        trace_finalize_timeout_seconds=args.trace_finalize_timeout,
        agent_timeout=args.timeout if args.timeout is not None else 900.0,
    )


def _print_result(result: CycleResult) -> None:
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.manifest.get("status"),
                "output_dir": str(result.output_dir),
                "manifest": str(result.output_dir / "manifest.json"),
                "error": result.manifest.get("error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    clean_local_agent_state()
    args = _build_parser().parse_args(argv)
    try:
        agent_names = [
            item.strip() for item in (args.agents.split(",") if args.agents else [args.agent]) if item.strip()
        ]
        unknown = set(agent_names) - set(SUPPORTED_AGENTS)
        if not agent_names or unknown:
            raise ValueError(f"unsupported agent(s): {sorted(unknown) or agent_names}")
        if args.task_file and args.url:
            raise ValueError("use either --task-file or --url, not both")
        if args.task_file:
            tasks = _apply_cli_defaults(_load_tasks(args.task_file), args)
        elif args.url:
            prompt = args.prompt
            if not prompt and set(agent_names) == {"manual"}:
                prompt = "手动操作网页并记录 fingerprint"
            if not prompt:
                raise ValueError("--prompt is required with --url for automated agents")
            tasks = _apply_cli_defaults([{"url": args.url, "prompt": prompt}], args)
        else:
            raise ValueError("provide --task-file or --url")
        if args.output_dir is not None and (len(tasks) != 1 or len(agent_names) != 1):
            raise ValueError("--output-dir is only valid for one task and one Agent; use --output-root for a matrix")

        runner = _build_runner(args)
        results: list[CycleResult] = []
        if len(tasks) == 1 and len(agent_names) == 1:
            task = tasks[0]
            result = runner.run_once(
                task["url"],
                task["prompt"],
                agent_name=agent_names[0],
                model=task.get("model"),
                run_id=args.run_id,
                output_dir=args.output_dir,
                upstream_model=task.get("upstream_model"),
                provider_key_env=task.get("provider_key_env"),
                route_alias=task.get("route_alias"),
                agent_timeout=task.get("agent_timeout"),
                overwrite=args.overwrite,
            )
            results.append(result)
        else:
            results = runner.run_matrix(tasks, agent_names)
        for result in results:
            _print_result(result)
        return 0 if all(result.success for result in results) else 1
    except KeyboardInterrupt:
        print("orchestrator: interrupted")
        return 130
    except Exception as exc:
        print(f"orchestrator: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
