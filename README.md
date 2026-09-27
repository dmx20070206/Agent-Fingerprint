# Agent-Fingerprint

这个项目把 Web Agent 的一次运行变成一个可归档的数据采集循环：同一个网页和任务可以依次交给 WebVoyager、Browser-use、Skyvern（或以后接入的其他 Agent），同时保存浏览器 UI 行为、LLM 请求延迟、网络抓包和子进程日志。

核心原则是：调度器只负责生命周期和数据编排，Agent 框架在自己的 Conda 环境中以子进程运行。调度器不会把 WebVoyager、Browser-use、Skyvern 的依赖导入到同一个 Python 进程，因此不同框架的依赖版本不会互相覆盖。

这里的“隔离”是依赖和进程隔离，不是安全沙箱：Agent 与调度器仍可使用同一用户、文件系统和网络，子进程也会继承父进程环境变量。尤其是 LiteLLM master key 会注入 Agent 子进程（它是本地代理认证/管理凭据），不要把运行目录或密钥暴露给不可信用户；需要真正的安全边界时，请再加容器、独立用户、只读文件系统和出站网络白名单。

> 当前仓库中的“完整流程测试”使用本地确定性的 Mock OpenAI 服务、真实 HTTP 沙盒、真实 Python 子进程和 fake tcpdump。它不需要 API key、浏览器、Conda 或 root 权限，验证的是工程边界和清理逻辑，不是模型能力或 Agent 基准成绩。

## 1. 架构总览

```text
                       ┌────────────────────────────┐
                       │       orchestrator.py       │
                       │  CLI / task matrix / routing│
                       └──────────────┬─────────────┘
                                      │
                       ┌──────────────▼─────────────┐
                       │       PipelineRunner         │
                       │  one cycle / cleanup / JSON  │
                       └───┬──────────┬──────────┬────┘
                           │          │          │
             ┌─────────────▼──┐ ┌─────▼────────┐ ┌▼─────────────────┐
             │ sandbox server  │ │ LiteLLM      │ │ TrafficSniffer   │
             │ static HTML +   │ │ subprocess   │ │ tcpdump/mitmdump │
             │ injected probe  │ │ :4000        │ │                  │
             └──────┬──────────┘ └─────┬────────┘ └────────┬─────────┘
                    │                  │                  │
                    │ UI events       │ request timing   │ PCAP/flow
                    ▼                  ▼                  ▼
          fingerprints/L1–L4       L4 agent trace    artifacts/network/PCAP
                                      ▲
                                      │ OpenAI-compatible HTTP
                    ┌─────────────────┴─────────────────┐
                    │  conda run ... Agent adapter child │
                    │  WebVoyager / Browser-use / custom │
                    └───────────────────────────────────┘
                                      │
                         manifest.json + stdout/stderr
```

一次运行的顺序是：

1. `PipelineRunner` 启动本地沙盒 HTTP Server。HTML 页面在返回前注入 `probes/inject_monitor.js`，并提供 UI 事件接收接口。
2. 为本轮创建独立的输出目录和 LiteLLM 配置副本；网关监听 `127.0.0.1:4000`（测试可以使用动态端口）。
3. 启动 `tcpdump` 或 `mitmdump`。
4. 适配器用无 shell 的 `conda run --name`，在各自专属环境中启动新的进程。主进程不 `import` Agent 框架源码。
5. 子进程访问沙盒页面、调用 `http://127.0.0.1:4000/v1` 的 OpenAI 兼容接口，并完成任务。
6. 网关回调在代理进程中记录请求开始/结束的纳秒时钟和毫秒延迟；探针把 click、scroll、input 等事件 POST 到沙盒的 `TraceStore`。
7. Agent 结束、失败或超时后按反向顺序停止抓包、网关和本轮自有的沙盒，最后写四层指纹文件；运行日志和元数据统一放入 `logs/`。

### 四层指纹开关与文件

四层采集默认全部开启，可在 CLI 或 `PipelineRunner` 中分别关闭：

```bash
python orchestrator.py --task-file tasks/tasks.jsonl \
  --no-collect-l1 --collect-l2=false --collect-l3 --no-collect-l4
```

每轮 `data/runs/<日期>/<run_id>/fingerprints/` 固定生成四个原始 JSON（关闭时保留
`enabled: false` 的空文档）：`l1_http_tls.json`（HTTP/TLS 抓包索引）、
`l2_browser_static.json`（浏览器静态属性）、`l3_browser_dynamic.json`
（UI 动态事件）和 `l4_agent_trace.json`（Agent 结果及 LLM 请求频率原始事件）。
完整 PCAP 位于 `artifacts/network/`；Agent 和网关的非空 stdout/stderr 位于
`logs/`。`data/runs/index.jsonl` 维护 run ID 到日期目录的索引。

## 2. 快速安装

根环境只负责调度、网关管理和测试。下面是一个方便开发的安装方式：

```bash
conda create -n af-orchestrator python=3.11 -y
conda activate af-orchestrator
python -m pip install -r requirements.txt
```

调度器和 LiteLLM 可以放在同一个轻量环境；必须分开的重点是两个 Agent 框架：

```bash
conda create -n webvoyager python=3.10 -y
conda activate webvoyager
python -m pip install -r lib/WebVoyager/requirements.txt
# Selenium 需要系统 Chrome/Chromium 与匹配的 driver；按机器发行版安装。

conda create -n browser-use python=3.11 -y
conda activate browser-use
python -m pip install -r requirements.browseruse.txt
browser-use install
```

适配器使用的环境名从 `config/config.yaml` 的 `conda.webvoyager`、`conda.browseruse` 和 `conda.skyvern` 读取。代码使用 `conda run --name`，不依赖 shell 中是否执行过 `conda activate`。
如果机器未安装 Conda，WebVoyager 适配器会使用当前 Python；此时应先在当前
`agent-fingerprint` 环境安装 `lib/WebVoyager/requirements.txt`。非标准安装位置
可通过 `WEBVOYAGER_CHROME_BINARY` 和 `WEBVOYAGER_CHROMEDRIVER` 指定浏览器与
driver。

Skyvern 安装在独立环境中。默认启动方式与其他 Agent 一致：调度器启动
LiteLLM，Skyvern 以本地 embedded 模式运行，并把模型请求发送到该网关：

```bash
conda create -n skyvern python=3.11 -y
conda run -n skyvern python -m pip install -r requirements.skyvern.txt
conda run -n skyvern python -m playwright install chromium
python orchestrator.py --agent skyvern --task-file tasks/external.jsonl \
  --model chat-gpt --no-network-probe
```

如果已有 Skyvern 环境使用 Playwright 1.46.0，并出现网页文字不显示、
`type()` / `fill()` 执行后输入框仍为空，可安装本项目验证过的浏览器依赖覆盖：

```bash
conda run -n skyvern python -m pip install -r requirements.skyvern-browser.txt
conda run -n skyvern python -m playwright install chromium
```

该文件只更新已有 Skyvern 环境的浏览器运行时；后续重新安装上游依赖若恢复了
旧版 Playwright，需要再次应用此覆盖。

此模式不需要 `SKYVERN_API_KEY`：adapter 使用临时内存数据库和本地浏览器，
`--model` 是发送给 LiteLLM 的公共 alias，具体云端模型仍由
`config/litellm.config.yaml` 或 `--upstream-model` 决定。每次运行的 Skyvern
`run_id`、状态和输出会归一化到根目录唯一的 `manifest.json`，SDK 仅在
Skyvern 子进程中导入。Embedded SDK 原本会在客户端关闭时删除临时产物；
适配器会先保存临时文件，流水线再将录像和逐步截图归档到：

```text
data/runs/<日期>/<run_id>/artifacts/
├── recordings/
│   └── *.mp4
└── screenshots/
    └── *.png
```

最终 manifest 的 `artifacts.recordings` 和 `artifacts.screenshots` 保存相对路径；
临时 `file://` URI 和 adapter 工作目录不会进入最终目录。

如需调用 Skyvern Cloud 或已有的自托管 Skyvern API，仍可使用旧模式：设置
`SKYVERN_API_KEY`（或传 `--skyvern-api-key`）以及可选的
`--skyvern-base-url`，并加 `--no-gateway --no-sandbox`。Cloud 无法访问
`127.0.0.1` 沙盒，因此任务 URL 必须能被 Cloud 访问；`--no-gateway` 模式
不能同时使用 `--upstream-model`。

Agent-E 使用上游提供的常驻 HTTP 服务。先在已安装 Agent-E 依赖和浏览器的
`agent-e` 环境中启动服务（上游当前会启动 headed Playwright 浏览器，因此需要
可用的图形会话或 Xvfb）：

```bash
cd lib/Agent-E
conda run -n agent-e --no-capture-output \
  uvicorn ae.server.api_routes:app --host 127.0.0.1 --port 8080 --loop asyncio
```

服务启动后，在项目根目录运行任务脚本：

```bash
AGENTE_ENDPOINT=http://127.0.0.1:8080/execute_task \
  bash scripts/agente/deepseek-chat/mouse_click.sh
```

Agent-E 已注册为 `--agent agente`。适配器会把本轮模型 alias、LiteLLM 地址和
临时网关密钥放入请求级 `llm_config`，因此 Agent-E 服务仍由独立环境运行，而
模型流量和延迟记录继续经过本轮 LiteLLM 网关。`scripts/browser-use/`、
`scripts/autogen/`、`scripts/skyvern/`、`scripts/webvoyager/` 与
`scripts/agente/` 均提供 `chat-gpt`、`deepseek-chat`、`claude`、`gemini`
四组七项任务和 `run_all.sh`；Agent-E 脚本默认连接上述 8080 端口，也可通过
`AGENTE_ENDPOINT` 覆盖。直接执行任一 `scripts/agente/<model>/*.sh` 时，脚本会
在 8080 端口不可用时自动启动 `agent-e` Conda 环境中的服务，并在任务结束后
关闭本次脚本启动的服务；已有服务只会复用，不会被脚本关闭。

Agent-E 可通过 `AGENTE_BROWSER_EXECUTABLE`（环境变量或 `lib/Agent-E/.env`）
指定 Chromium 可执行文件，此设置优先于 `AGENTE_BROWSER_CHANNEL`。在本机，
旧 Chromium 125 会出现输入操作成功但输入框仍为空的问题；Chromium 151 已验证
可正常输入。保留 Agent-E 原有 Playwright 1.44，因为它使用旧版 accessibility
API；只更换浏览器可执行文件。`chat-gpt/flights.sh` 默认允许运行 3600 秒，
可通过 `AGENTE_FLIGHTS_TIMEOUT` 调整，以容纳规划器与浏览器代理的多轮请求。

例如，使用环境名或环境目录分别写成：

```bash
python orchestrator.py --browseruse-conda-env browser-use ...
# 矩阵中分别指定两个环境：
python orchestrator.py --agents webvoyager,browseruse \
  --webvoyager-conda-env webvoyager \
  --browseruse-conda-env browser-use ...
```

系统层面还需要：

* `tcpdump`：通常需要 root 或 `CAP_NET_RAW`/`CAP_NET_ADMIN`。没有权限时用 `--no-network-probe`，或在测试中注入 fake sniffer。
* `mitmdump`：只有选择 `--network-backend mitmproxy` 时才需要；浏览器还要显式配置代理。
* Chrome/Chromium：WebVoyager 通过 Selenium 启动；固定在本项目依赖范围内的 Browser-use 可用 `browser-use install` 安装所需 Chromium。

根 `requirements.txt` 只引用 `requirements.orchestrator.txt`。两个 Agent 必须分别安装各自的 requirements；这不是可选优化，而是避免依赖冲突的隔离边界。

## 3. 手动操作网页并记录 fingerprint

人工采集不需要 LLM、API key 或 Agent 专用 Conda 环境。下面的命令会启动本地沙盒，向 HTML 注入采集探针，并用系统默认浏览器打开页面：

```bash
python orchestrator.py --manual --url /mouse-click.html
```

页面加载后可立即操作。完成后回到运行命令的终端按 Enter，程序才会停止 collector 并落盘；若页面从未向 collector 上报事件，本次运行会明确标记为失败，避免把空 fingerprint 当成成功结果。终端会打印本次输出目录，其中包含：

```text
fingerprints/l1_http_tls.json
fingerprints/l2_browser_static.json
fingerprints/l3_browser_dynamic.json
fingerprints/l4_agent_trace.json
manifest.json
result.json
```

`l2_browser_static.json` 保存浏览器、屏幕、时区、WebGL 等静态属性；`l3_browser_dynamic.json` 保存鼠标移动、点击、滚动、键盘及 DOM 变化事件。人工模式默认关闭 tcpdump，适合普通桌面环境；需要同时采集网络层时加上 `--manual-network-probe`（没有 tcpdump 权限时保持关闭）。

要操作自己的本地静态站点，将站点目录作为沙盒根目录：

```bash
python orchestrator.py --manual \
  --sandbox-directory /absolute/path/to/site \
  --url /index.html
```

可用 `--manual-browser firefox` 选择 Python 已注册的浏览器。若运行环境不能自动唤起 GUI，可加 `--manual-no-open`，复制终端打印的 URL 到浏览器；采集流程保持不变。人工模式会自动禁用 LiteLLM 网关。

所有访问项目本地沙盒并启用 monitor 的 Agent 默认都不等待。调试时可以通过 `--interaction-delay <秒数>` 显式启用交互闸门。外部网站无法由本地沙盒注入脚本，因此不受该闸门控制。

## 4. 先运行不花钱的完整测试

```bash
conda activate af-orchestrator
python -m pytest -q
```

根环境建议使用 Python 3.11 或更高版本（当前 LiteLLM proxy 发行版需要它）。若已经安装了 LiteLLM，并希望额外验证“真实 LiteLLM → 本地 Mock 上游”，可运行可选测试：

```bash
AGENT_FINGERPRINT_LITELLM_SMOKE=1 python -m pytest -q test/test_litellm_optional.py
```

未设置开关时该测试会自动跳过，不影响普通离线测试。

测试分层如下：

* `test/test_pipeline_integration.py`：真实启动沙盒 HTTP Server，真实启动一个 Python 子进程 Agent；Fake Gateway 提供 `/v1/chat/completions` 并写延迟 JSONL，FakeCapture 生成 PCAP 形状的文件。还验证 Agent 失败时的清理顺序。
* `test/test_pipeline_mock_gateway.py`：使用仓库自带的 `MockOpenAIGateway`，验证一个 OpenAI 兼容本地服务如何接入 `PipelineRunner`。
* `test/test_litellm_optional.py`：可选地启动真实 LiteLLM 代理，并把路由指向本地 Mock 上游；不需要 provider key。
* `test/test_cli_e2e.py`：从公开 CLI 启动 sandbox + Mock Gateway + 真实子进程 Mock Agent，并检查 UI/延迟/manifest 产物。
* 其他 `test/test_*.py`：分别覆盖 YAML 路由、适配器命令、UI/网络探针、沙盒注入和 CLI 辅助函数。

这个 Mock 服务返回固定的 `DONE`，不是免费的大模型。公共“免费 LLM API”没有稳定的额度、服务质量和隐私保证，所以 CI 不依赖任何公共账号。需要真正的本地模型时，可以把 LiteLLM 的上游配置改为 Ollama/vLLM，但那仍需要在本机下载模型和占用显存。

也可以单独运行 Mock 服务观察协议：

```bash
python -m gateway.mock_openai --port 4010 --output-dir data/mock-gateway --delay-ms 20
# OpenAI-compatible base URL: http://127.0.0.1:4010/v1
```

如果希望连同真实 LiteLLM 代理一起做本地集成（仍不访问付费 provider），可先启动 Mock 服务，再使用 `config/litellm.mock.yaml`。请使用两个终端（Mock 服务会一直前台运行）：

终端 A：

```bash
python -m gateway.mock_openai --port 4010 --output-dir data/mock-upstream
```

终端 B：

```bash
export LITELLM_MASTER_KEY='sk-local-master-key'
export MOCK_LLM_API_BASE='http://127.0.0.1:4010/v1'
export MOCK_LLM_API_KEY='mock-key'
python orchestrator.py --gateway-config config/litellm.mock.yaml \
  --agent webvoyager --url /01-minimal.html --prompt '点击确认' \
  --no-network-probe
```

结束时分别在两个终端按 Ctrl-C。这一步需要在网关环境安装 `litellm[proxy]`，但上游响应仍由本地固定文本服务产生；命令中的 `--agent webvoyager` 仍额外要求 WebVoyager Conda 环境和 Chrome。如果只想验证网关本身，优先运行前面的可选 `test/test_litellm_optional.py`。

## 5. 运行一次真实 Agent 循环

可将密钥保存在项目根目录的 `.env` 文件中，调度器会自动加载（该文件已被 `.gitignore` 忽略）。可参考 `.env.example`。`LITELLM_MASTER_KEY` 仅用于本地网关认证；`RELAY_OPENAI_API_KEY`/`ANTHROPIC_API_KEY` 才是访问云端模型的提供商密钥。

先准备网关密钥。网关的 master key 用于 Agent 到本地代理的认证，并应按管理员级本地凭据保护；真正的上游 key 由 `config/litellm.config.yaml` 中的环境变量引用：

```bash
export LITELLM_MASTER_KEY='sk-local-master-key'
export RELAY_OPENAI_API_KEY='sk-...'
```

WebVoyager 示例：

```bash
python orchestrator.py \
  --agent webvoyager \
  --url /01-minimal.html \
  --prompt '点击确认按钮并报告页面变化' \
  --model chat-gpt \
  --output-root data/runs
```

每个 Agent 的四个模型目录均提供 flights、forums、shop 及四种基础交互任务；
各目录的 `run_all.sh` 可顺序执行整组任务。例如 WebVoyager 的目录为
`scripts/webvoyager/{chat-gpt,deepseek-chat,claude,gemini}/`。

Browser-use 示例：

```bash
python orchestrator.py \
  --agent browseruse \
  --url /02-dense.html \
  --prompt '找到提交按钮并点击' \
  --model chat-gpt \
  --timeout 600
```

如果当前机器没有 tcpdump，可以先确认 Agent/网关流程：

```bash
python orchestrator.py \
  --agent webvoyager \
  --url /01-minimal.html \
  --prompt '读取页面标题' \
  --no-network-probe
```

需要缩小 tcpdump 采集范围时，例如只看本地网关和沙盒端口：

```bash
python orchestrator.py --agent mock --mock-llm \
  --url /01-minimal.html --prompt 'smoke test' \
  --network-interface lo \
  --capture-filter 'host 127.0.0.1'
```

端口过滤器应按本轮实际绑定的沙盒/网关端口填写；`port=0` 的 Mock smoke 会动态分配端口。

`--mock-llm` 会把网关替换为本地确定性服务，不需要 `LITELLM_MASTER_KEY` 或上游 key。它只实现 OpenAI Chat Completions 的传输形状，不具备视觉理解、工具规划或 Browser-use 的结构化动作能力。因此真实 WebVoyager/Browser-use 最多可用它验证“请求是否到达网关”和失败清理，不能期望完成网页任务。例如：

```bash
python orchestrator.py --mock-llm --no-network-probe \
  --agent browseruse --url /01-minimal.html --prompt '点击确认'
```

这个命令还要求 Browser-use 及浏览器已经装在专属环境，而且很可能因固定回复无法解析为动作而以 failed manifest 结束；这是预期的协议/失败路径测试。

若只想在没有浏览器和 Conda 的机器上验证 CLI 的全部生命周期，可使用内置的 `mock` Agent（它会在子进程中发起 GET、提交一个合成 click，再调用 Mock 网关；不代表真实 Agent 能力）：

```bash
python orchestrator.py --agent mock --mock-llm --no-network-probe \
  --url /01-minimal.html --prompt 'smoke test' --output-root data/mock-runs
```

### FP-Agent 测试站点

FP-Agent 实验框架的网页已按 Wikipedia 的组织方式拆为三个独立站点：`sandbox/flights/`、`sandbox/forums/` 和 `sandbox/shop/`。每个目录都可单独作为沙盒根目录启动；原有采集端点不需要 PostgreSQL，项目自己的注入探针仍负责记录浏览器行为。

可直接运行配套的三个任务：

```bash
python orchestrator.py --task-file tasks/flights.jsonl --sandbox-directory sandbox/flights \
  --agent browseruse --no-network-probe
python orchestrator.py --task-file tasks/forums.jsonl --sandbox-directory sandbox/forums \
  --agent browseruse --no-network-probe
python orchestrator.py --task-file tasks/shop.jsonl --sandbox-directory sandbox/shop \
  --agent browseruse --no-network-probe
```

`sandbox/static/mouse-move.html` 至 `13-wheel-scroll.html` 分别隔离测试鼠标移动、
鼠标点击、键盘打字和鼠标滚轮。可运行对应的 `scripts/browseruse_mouse_move.sh`、
`browseruse_mouse_click.sh`、`browseruse_keyboard_type.sh` 和
`browseruse_wheel_scroll.sh`，也可以一次顺序执行四项任务：

```bash
bash scripts/browseruse_input_interactions.sh
```

本地查看页面：

```bash
python -m sandbox.server --directory sandbox/flights --port 8000 --inject-monitor
# http://127.0.0.1:8000/
```

运行结束后 CLI 会打印一行 JSON，例如：

```json
{"run_id":"20260905T...-a1b2c3d4","status":"success","output_dir":".../data/runs/...","manifest":".../manifest.json","error":null}
```

## 6. 批量矩阵

`--task-file` 支持 JSON 数组、单个 JSON 对象或 JSONL。字段可以写成项目内部格式 `url/prompt`，也兼容 WebVoyager 的 `web/ques`：

```jsonl
{"url":"/01-minimal.html","prompt":"点击确认","model":"chat-gpt"}
{"url":"/02-dense.html","prompt":"滚动到底部","model":"claude-sonnet","upstream_model":"anthropic/claude-sonnet-4-6","provider_key_env":"ANTHROPIC_API_KEY"}
```

```bash
python orchestrator.py \
  --task-file tasks.jsonl \
  --agents webvoyager,browseruse \
  --output-root data/matrix
```

矩阵目前按任务、Agent 顺序执行，不支持并发复用固定的 `:4000` 端口；这样可以避免两个 Agent 的 CPU、浏览器和时间戳互相污染。每个组合都有独立目录。

## 7. 动态选择 Agent 和模型

代码中的统一接口是：

```python
from pipeline import PipelineRunner

runner = PipelineRunner(
    output_root='data/runs',
    gateway_config='config/litellm.config.yaml',
)
result = runner.run_once(
    '/01-minimal.html',
    '点击确认按钮',
    agent_name='webvoyager',
    model='chat-gpt',       # Agent 看到的公开 alias
    upstream_model='anthropic/claude-sonnet-4-6',
    provider_key_env='ANTHROPIC_API_KEY',
    route_alias='chat-gpt',
)
```

每轮会把源 YAML 复制到 `run_dir/litellm.config.yaml`，再修改这个副本；仓库中的 `config/litellm.config.yaml` 不会被路由切换污染。LiteLLM 仍让 Agent 以为自己在调用 OpenAI，实际根据 alias 转发到 OpenAI、Anthropic、Ollama 或其他兼容后端。

如果要注册自定义框架，不需要修改 `PipelineRunner`：

```python
runner = PipelineRunner(
    gateway_factory=lambda run_dir: make_my_gateway(run_dir),
    adapter_factory={
        'my-agent': lambda model=None, gateway=None, output_dir=None:
            MyAdapter(model=model, base_url=gateway.base_url),
    },
)
runner.run_once('/01-minimal.html', '完成任务', agent_name='my-agent')
```

工厂既可以是零参数，也可以声明 `run_dir`、`model`、`gateway`、`output_dir` 等上下文参数。工厂内部抛出的 `TypeError` 不会被调度器错误地当成签名不匹配。

自定义 `gateway_factory` 返回的对象由调用方负责提供隔离语义；如果它只是一个共享的、已运行的进程，Pipeline 不能替它复制配置或保证不同轮次之间没有状态泄漏。内置 `GatewayConfig`/`LiteLLMGateway` 路径才会自动为每轮复制 YAML。

## 8. 输出目录和 manifest

一次成功运行的典型布局：

```text
data/runs/<日期>/<run_id>/
├── manifest.json
├── fingerprints/
│   ├── l1_http_tls.json
│   ├── l2_browser_static.json
│   ├── l3_browser_dynamic.json
│   └── l4_agent_trace.json
├── artifacts/
│   ├── recordings/
│   ├── screenshots/
│   ├── downloads/
│   ├── framework/
│   └── network/traffic.pcap
└── logs/
    ├── agent.stdout.log
    ├── agent.stderr.log
    ├── gateway.stdout.log
    ├── gateway.stderr.log
    ├── network.stdout.log
    └── network.stderr.log
```

`manifest.json` 的关键字段：

* `status`：只有 Agent 成功且抓包没有失败时才是 `success`。
* `task.requested_url`/`task.url`：分别是调用方传入的 URL 和解析后的 URL。相对路径会指向本轮临时沙盒端口。
* `outcome`：Agent 的归一化输出、框架状态、失败原因和步数。
* `execution`：子进程返回码、耗时和命令摘要哈希；不会嵌入 inline runner 源码。
* `gateway.route`：本轮 alias、上游模型和 key 环境变量（不保存 key 本身）。
* `gateway.latency_event_count`：已折叠进 L4 的网关事件数；临时 JSONL 不重复保留。
* `fingerprints`、`artifacts`、`logs`：三类文件的相对路径；`files` 是完整规范文件清单。
* `cleanup_errors`：停止服务或写文件时发生的附加错误；主错误不会被清理错误覆盖。

四个格式化脚本只需要传任务 id；它们通过 `data/runs/index.jsonl` 定位日期目录，
并写到 `data/results/<id>`：

```bash
python analysis/l1_http_tls.py <id>
python analysis/l2_browser_static.py <id>
python analysis/l3_browser_dynamic.py <id>
python analysis/l4_agent_trace.py <id>
```

四个脚本都支持 `--input-dir` 和 `--output-dir` 覆盖默认目录；结果文件名与
四个原始文件一致，但 schema 为格式化后的 L1–L4 模型输入格式。

显式传入 `--output-dir` 时，默认拒绝复用非空目录，避免一次采集覆盖另一次采集；确认要复用时才加 `--overwrite`。不传该参数时，新运行写入 `--output-root/<日期>/<run_id>`。旧 v1 目录可先预览、再显式迁移：

```bash
python scripts/migrate_runs_v1_to_v2.py --root data/runs
python scripts/migrate_runs_v1_to_v2.py --root data/runs --apply
```

## 9. 每个目录/文件做什么

| 文件 | 作用 |
| --- | --- |
| `orchestrator.py` | 完整 CLI、JSON/JSONL 任务加载、Agent 矩阵、LiteLLM/Mock 网关工厂。 |
| `pipeline.py` | `PipelineRunner` 和 `CycleResult`。控制启动顺序、相对 URL 解析、动态路由、超时、反向清理、v2 manifest 和紧凑产物布局。也可用 `python -m pipeline` 作为 CLI 别名。 |
| `requirements.txt` | 根环境依赖入口，只引用调度器依赖文件，不把两个 Agent 框架装进根环境。 |
| `requirements.orchestrator.txt` | 调度器、LiteLLM proxy、YAML 和 pytest 依赖（Python 3.11+）。 |
| `requirements.webvoyager.txt` / `requirements.browseruse.txt` / `requirements.skyvern.txt` | 分别供独立 Conda 环境安装的框架依赖。 |
| `environment.yml` | 可直接用 `conda env create -f environment.yml` 创建根调度环境。 |
| `adapters/base_adapter.py` | 统一 `run_task(url, prompt, output_dir)` 协议；构造无 shell 的 `conda run` 命令、保存日志、处理超时并终止整个子进程组。 |
| `adapters/browseruse_adapter.py` | 在 Browser-use Conda 子进程的 stdin 中执行短 runner；只有子进程才 `import browser_use`。支持 OpenAI-compatible `base_url`、模型 alias、代理、最大步数，并默认关闭匿名 telemetry；同时读取 Browser-use 的任务成功 verdict。 |
| `adapters/webvoyager_adapter.py` | 为 `lib/WebVoyager/run.py` 生成单行 JSONL，然后在 WebVoyager Conda 环境执行；把 `OPENAI_BASE_URL` 指向 LiteLLM，并默认通过环境变量传 key，避免把密钥放进命令行。 |
| `adapters/agente_adapter.py` | Agent-E HTTP 适配器。它要求先在 Agent-E 环境启动 `/execute_task` 服务，并把当前模型 alias 和 LiteLLM 网关作为请求级 `llm_config` 发送给服务。 |
| `adapters/mock_adapter.py` | 无浏览器的确定性子进程 Agent，只用于验证 CLI/HTTP/产物生命周期；不代表模型或 Web Agent 能力。 |
| `adapters/skyvern_adapter.py` | 在 Skyvern 独立环境中运行 embedded SDK，并通过实验 LiteLLM 网关调用模型；也兼容 Cloud/自托管 API、超时和最大步数。 |
| `gateway/litellm_gateway.py` | 读取/校验 YAML、每轮隔离配置、启动/健康检查/停止 LiteLLM 子进程、切换 alias 和保存运行元数据。 |
| `gateway/latency_callback.py` | LiteLLM `CustomLogger`。记录 request start/end/error、纳秒时钟、毫秒延迟和 usage；递归过滤 prompt、message、content、key 等敏感字段，并只对确认没有 pending start 的重复 terminal hook 做上下文去重。每轮网关启动时会把它复制到该轮 YAML 旁的 `gateway/`，兼容按 config 目录加载 callback 的 LiteLLM 版本。 |
| `gateway/mock_openai.py` | 无依赖的本地 OpenAI-compatible `/v1/chat/completions` 服务；返回固定文本并写延迟 JSONL，只用于测试。 |
| `config/litellm.config.yaml` | 公共 alias 到 OpenAI/Anthropic 上游的示例映射。只引用 `os.environ/变量名`，不放真实密钥。 |
| `config/litellm.mock.yaml` | 将 alias 路由到本地 Mock OpenAI 服务的 LiteLLM 配置。 |
| `config/tasks.example.jsonl` / `config/prompts.md` | 批量任务样例和九类沙盒页面的任务提示。 |
| `probes/inject_monitor.js` | 浏览器端 UI 探针，监听 click、scroll、focus、input、submit，并以节流方式记录 pointermove 轨迹；输入框只记录长度、不记录值，失败批次会重试。 |
| `probes/traffic_sniffer.py` | `tcpdump`/`mitmdump` 生命周期封装，写当前网络命名空间/所选接口可见的 PCAP 或 flow、stdout/stderr 和结构化元数据，不使用 `shell=True`；支持 BPF filter。 |
| `sandbox/server.py` | 标准库 Threading HTTP Server，提供 `sandbox/static`，可注入探针，接收/导出/去重 UI 事件。它是本地实验 collector，不是带认证和限流的生产服务。 |
| `sandbox/static/*.html` | 可控的合成网页：极简、高密度、深 DOM、迷宫、延迟、诱导按钮、拖拽等不同元素密度和交互时序。 |
| `test/test_pipeline_integration.py` | 跨 HTTP/子进程边界的离线完整循环测试。 |
| `test/test_pipeline_mock_gateway.py` | 使用 Mock OpenAI 服务的完整循环测试。 |
| `test/test_litellm_optional.py` | 在显式开启开关时，用真实 LiteLLM 转发到本地 Mock 上游的冒烟测试。 |
| `test/test_*.py` | 各层单元测试和安全边界测试。 |
| `pytest.ini` | 将 pytest 收集范围固定为 `test/`。 |
| `lib/WebVoyager/`、`lib/Agent-E/` | 研究型框架的独立 checkout。适配器只通过入口/HTTP 与它们交互，不把它们的依赖导入调度器。 |

## 10. LiteLLM 配置和日志

配置示例：

```yaml
model_list:
  - model_name: chat-gpt
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/RELAY_OPENAI_API_KEY
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY
```

`LiteLLMGateway.start()` 会在本轮配置中加入 master key 和 callback，把 callback package 复制到 YAML 同目录，并把 `PYTHONPATH` 指向项目根；这样同时兼容按 config 目录和按 Python path 加载 callback 的 LiteLLM 版本。回调的起点是 LiteLLM provider-call hook，而不是客户端发出 HTTP 请求的绝对时刻；记录的是 proxy/provider-call 边界内的 latency，可能包含网络和 provider 排队，不应误称为纯 GPU 推理时间。CLI 要求 master key 以 `sk-` 开头；它会被传给 Agent 子进程并具有本地代理管理语义，不能替代上游 provider key，也不应暴露给不可信 Agent。

Browser-use 适配器默认设置 `ANONYMIZED_TELEMETRY=false`，使实验日志不额外发送匿名 telemetry；如确实需要上游 telemetry，可在构造器中传 `disable_telemetry=False`。

Browser-use 依赖在 `requirements.browseruse.txt` 中限定为 `0.13.x`，因为其公开 Python API 和浏览器启动方式仍在快速演进。升级到新的 minor 版本时，应先重跑适配器与真实浏览器集成测试。配置里的上游模型名称同样只是示例；实际运行前请按供应商账户当前可用的多模态模型修改。

不要把 `data/` 提交到公共仓库：

* manifest 会保存任务 prompt、URL、命令和错误摘要；Agent/LiteLLM 的专用日志也可能包含页面文本、URL、异常正文或 provider 返回内容；
* PCAP 可能包含 Authorization、API key 或请求正文；
* UI 探针会保存目标元素的短文本/aria 标签（普通 input/textarea 的 value 不记录，但 `contenteditable` 或元素可见文本仍可能含输入内容），浏览器截图/下载物也可能含个人信息；
* 页面截图、DOM/无障碍树和 prompt 还会发送给配置的模型 provider，不能因为网关在本机就视为“完全本地”。

代码不会强制 URL 白名单、下载限制或 prompt-injection 防护；外部 URL 可能引导 Agent 提交数据、访问内网或下载文件。只在本地或合成网页上采集，或在容器/浏览器 context/网络防火墙层增加允许域名、出站限制和人工审批，并按组织的密钥和数据保留策略清理产物。

## 11. UI 探针和网络探针的边界

沙盒 Server 只对它自己提供的 HTML 做注入，并提供 `/__agent_fingerprint__/events` 接口。为了让合成页和测试方便，collector 默认无认证、允许 `*` CORS，客户端还可以自报 `run_id`；它没有生产级请求体/事件数限流。请只绑定回环地址并用防火墙/反向代理限制访问，不要把它直接暴露到共享网络。当前实现把 collector URL 写成绝对地址并开启了最小 CORS/OPTIONS 支持，因此跨域 POST 在技术上可被接受；但是页面导航到另一个 origin 后，原页面的 JavaScript 上下文会被销毁，探针不会自动跟随到新站点。若要测量 WebVoyager 访问的外部站点，应在 Playwright/Selenium browser context 层用 `addInitScript`/CDP 对每个新文档注入，或只把本地合成页作为实验对象。

`tcpdump` 是默认的低层抓包路径；它只能看到当前网络命名空间和所选接口对当前用户可见的流量，并不保证跨容器/跨主机的“所有流量”。默认接口是 `any`，可能把同一命名空间其他进程的流量也录入；可用 `--network-interface` 和 `--capture-filter` 缩小范围。PCAP 可能包含明文请求或凭据，输出目录权限和保留周期需由部署者管理。`mitmproxy` 是显式代理模式：Pipeline 会把代理地址传给适配器并清空 `NO_PROXY/no_proxy`，Chrome 通常还需要 `--proxy-server=...`，首次 HTTPS 解码还需要在浏览器环境信任 mitmproxy CA；LiteLLM 的上游请求不会自动经过该 Agent 代理。因此需要 HTTP 解码/请求级记录时再选择 mitmproxy；要覆盖更多底层流量时优先使用有权限的 tcpdump，并在容器部署中配置正确的网络命名空间/接口。
本项目统一使用 tcpdump 进行网络流量采集。

## 12. 常见问题

**为什么 `PipelineRunner()` 没有启动 LiteLLM？**

库 API 默认 `gateway_config=None`，方便单元测试和注入 fake gateway；完整 CLI 会自动构造 gateway factory。嵌入式调用请显式传 `gateway_config='config/litellm.config.yaml'` 或 `gateway_factory=...`。

**为什么默认没有安装 Agent 框架到根环境？**

默认配置保留隔离：Browser-use 和 WebVoyager 分别安装在自己的 Conda 环境，
由 `conda run` 启动。没有 Conda 时，WebVoyager 可回退到当前解释器，前提是
当前环境已经安装它的依赖；Browser-use 仍建议使用独立环境。

**LiteLLM 启动失败怎么办？**

先查看本轮的 `logs/gateway.stderr.log`，确认 `LITELLM_MASTER_KEY` 已设置、provider key 环境变量存在、`litellm` 可执行文件在对应环境的 PATH 中，并确认 `:4000` 没有被占用。

**tcpdump 报 permission denied 怎么办？**

给可执行文件配置受控 capability，或用 `--no-network-probe` 验证其余链路。不要为了测试把整个调度器以 root 长期运行。

**UI trace 为空怎么办？**

确认 URL 是沙盒相对路径或本地沙盒绝对 URL、没有传 `--no-monitor`，并检查 `fingerprints/l3_browser_dynamic.json` 与 Server 日志。外部站点导航需要 browser-context 级注入，见上一节。

**Agent 超时后会不会留下 Chrome/子进程？**

`BaseAgentAdapter` 为子进程建立独立 process group，超时先发 SIGTERM，必要时 SIGKILL；浏览器由 Agent 子进程负责关闭。若框架创建了脱离 process group 的守护进程，仍应在该框架自己的 cleanup hook 中处理。

**按 Ctrl-C 会怎样？**

当前适配器会先终止正在运行的子进程组、保存失败 manifest，再把中断传回 CLI；批量矩阵不会在用户要求停止后继续下一个任务，CLI 返回码为 130。

## 13. 当前实现范围

默认 CLI 注册了以下浏览器 Agent：

* **WebVoyager**：研究型、源码入口清晰但依赖固定，适配器通过 JSONL + `run.py` 调用；
* **Browser-use**：库型、通过 stdin runner 在独立环境导入，适合作为工程化 baseline。
* **Skyvern**：以 embedded SDK 运行，使用本地浏览器和本轮 LiteLLM 网关；
* **AutoGen**：按模型能力在视觉 browser surfer 与纯文本浏览器之间切换；
* **Agent-E**：通过预先启动的 `/execute_task` HTTP 服务运行，服务生命周期独立于单次采集；
* `mock`：仅用于离线生命周期测试，不是 Web Agent。

Agent-E 仍不是“一次命令启动全部组件”的框架：任务脚本负责本轮采集，HTTP
服务需要提前启动并可被 `AGENTE_ENDPOINT` 访问。新增其他研究代码时，只需实现
`BaseAgentAdapter.run_task()`，或者注册一个自定义 factory，核心调度器不需要
知道其内部依赖。

Conda 环境解决的是“哪个依赖被加载”的问题，不提供权限边界；如果 Agent 不可信，请使用容器/独立 UID、最小化环境变量（不要把所有云凭据继承给子进程）、限制浏览器下载目录和网络出口。`--mock-llm` 与本地 Mock 服务只验证协议和清理，不验证视觉理解、规划质量或真实框架成功率。
