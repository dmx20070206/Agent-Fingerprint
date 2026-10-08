# 采集：单次任务与批量实验

[文档导航](README.md) · 前置：[安装与模型配置](setup.md) · 下一步：[构建数据集](data-contract.md)

`collect` 执行任务并保存原始记录；`experiment` 将配置中的框架、模型、任务展开成多次 `collect`。
真实框架需要自己的运行环境和模型配置；无需密钥的流程示例见[项目首页](../README.md)。

| 场景 | 选择 |
| --- | --- |
| 一个框架运行一个任务文件 | `collect --agent ... --task-file ...` |
| 多框架、多模型、多次重复 | `experiment`，先加 `--dry-run` 预览 |
| 自己操作网页 | `collect --manual` |

所有命令在仓库根目录执行。一次任务执行称为一个 run；一个任务文件可以产生多个 run。

## 单次采集

```bash
python -m agent_fingerprint collect \
  --agent browseruse --model claude \
  --task-file tasks/flights.jsonl --sandbox-directory sandbox/flights \
  --site flights --collector semantic/v1 \
  --timeout 900 --max-steps 100 --no-network-probe
```

默认输出到 `data/runs/<日期>/<run_id>/`，终端输出 `run_id`、`status`、`output_dir`、`manifest` 和错误信息。
示例选择 Browser-use + claude，最多运行 900 秒或 100 步，并采集增强语义事件；它需要真实模型密钥和 Browser-use 环境。
任务文件每行一个 JSON 对象，使用 `url`、`prompt` 字段，也兼容 `web`、`ques`：

```json
{"url": "/01-minimal.html", "prompt": "点击页面上的按钮"}
```

也可用 `--url /01-minimal.html --prompt '点击页面上的按钮'` 直接传任务。相对 URL 由本地网页服务提供，不能同时指定 `--url` 和 `--task-file`。

| 常用参数 | 默认值 / 用途 |
| --- | --- |
| `--agent` | 默认 `webvoyager`；可选 `browseruse/webvoyager/skyvern/autogen/agente/manual/mock` |
| `--model` | 传给 Agent 的模型别名，对应网关路由 |
| `--task-file` | JSON/JSONL 任务文件；多条任务依次执行 |
| `--url`、`--prompt` | 直接指定网页和任务；相对 URL 对应本地网页根目录 |
| `--sandbox-directory` | 本地网页根目录；flights 使用 `sandbox/flights` |
| `--collector` | 默认 `legacy/v2`；`semantic/v1` 额外采集语义解析需要的结构信息 |
| `--timeout` | 单任务超时秒数；未指定时为 1800 |
| `--max-steps` | 默认 100；Browser-use、Skyvern、AutoGen、Agent-E 的步数/轮数上限 |
| `--max-iter` | 默认 100；WebVoyager 的迭代上限 |
| `--output-root` | 默认 `data/runs`；自动创建各次运行目录 |
| `--output-dir` | 精确指定单次输出目录；仅支持一个任务和一个 Agent |
| `--run-id` | 单次运行的稳定 ID；与任务 ID 不同 |
| `--task-id`、`--site` | 显式任务 ID、网页集合标签；未指定时推导 |
| `--no-network-probe` | 跳过 tcpdump，适合只研究 L3 浏览器行为 |
| `--no-collect-l1` … `--no-collect-l4` | 关闭某层采集；默认四层均开启 |
| `--interaction-delay` | 每次打开受监测页面后禁用交互的秒数，默认 0 |

`--config` 指定框架环境配置，`--gateway-config` 指定模型路由配置，默认均在 `configs/` 下。
只替换一次运行的上游路由时，可加 `--upstream-model` 和 `--provider-key-env`。框架专用参数用 `collect --help` 查看。

## 人工采集

```bash
python -m agent_fingerprint collect \
  --manual --url /11-mouse-click.html --collector semantic/v1
```

命令会打开页面，按终端提示操作并结束。人工模式默认不启动模型网关，也不抓包。`--manual-no-open` 只打印 URL；`--manual-browser chromium` 指定浏览器。

## 批量实验

```bash
# 先检查计划：2 个框架 × 1 个模型 × 2 个任务 × 3 次重复 = 12 条采集命令
python -m agent_fingerprint experiment \
  --agents browseruse,webvoyager --models claude \
  --tasks flights,shop --repeats 3 --dry-run

# 执行同一计划
python -m agent_fingerprint experiment \
  --agents browseruse,webvoyager --models claude \
  --tasks flights,shop --repeats 3
```

若任务文件包含多条任务，每条采集命令还会依次执行文件中的任务。

| 参数 | 说明 |
| --- | --- |
| `--agents`、`--models`、`--tasks` | 逗号分隔，必须是配置中的名称；省略时使用 matrix |
| `--repeats` | 每种组合重复次数；当前配置默认 5 |
| `--config` | 默认 `configs/experiments/default.yaml` |
| `--dry-run` | 输出完整采集参数，不启动 Agent |
| `--report` | 指定计划和返回码报告路径；默认在 `data/experiments/<时间戳与随机后缀>/collection.json` |

当前默认矩阵为 5 个框架 × 4 个模型 × 3 个任务 × 5 次重复，即 300 条采集命令。
`--tasks forums` 是实验任务名，保存的数据网页标签为 `forum`；筛选数据时使用 `--sites forum`。

超时、采集器、抓包等 `collect` 选项在实验 YAML 中配置，不直接传给 `experiment`。覆盖顺序：`defaults → task → agent → model → overrides`。
当前默认矩阵关闭抓包、超时 900 秒，部分组合会覆盖该值。现有 `scripts/exp/` 等 Shell 脚本是组合快捷入口。

常用任务与本地网页的对应关系：

| experiment 的 --tasks | collect 的 --task-file | collect 的 --sandbox-directory | 数据中的 site |
| --- | --- | --- | --- |
| `flights` | tasks/flights.jsonl | sandbox/flights | flights |
| `shop` | tasks/shop.jsonl | sandbox/shop | shop |
| `forums` | tasks/forums.jsonl | sandbox/forums | forum |
| `keyboard` | tasks/keyboard_type.jsonl | sandbox/static | keyboard |
| `mouse_click` | tasks/mouse_click.jsonl | sandbox/static | mouse_click |
| `mouse_move` | tasks/mouse_move.jsonl | sandbox/static | mouse_move |
| `wheel_scroll` | tasks/wheel_scroll.jsonl | sandbox/static | wheel_scroll |

批量命令会自动带入这些路径。`bash scripts/run_experiment.sh ...` 与 `python -m agent_fingerprint experiment ...` 使用相同参数。

## 去哪里看结果

先看 `manifest.json` 中的状态与错误，再查 `logs/` 和 `artifacts/`。
L3 原始事件位于 `fingerprints/l3_browser_dynamic.json`；运行状态为 success 不自动代表任务每个字段都正确，完成证据需结合页面和日志核对。
目录与字段说明见[数据契约](data-contract.md)。
