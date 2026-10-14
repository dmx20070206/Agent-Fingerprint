# 采集参数与排错

[文档导航](../README.md) · 常用命令见[工作流](../workflow.md#采集)。所有命令在仓库根目录的 `agent-fingerprint` 环境执行。

## 单次采集参数

| 常用参数 | 默认值 / 用途 |
| --- | --- |
| `--agent` | 默认 `webvoyager`；可选 `browseruse/webvoyager/skyvern/autogen/agente/manual/mock` |
| `--model` | 传给 Agent 的模型别名，对应网关路由 |
| `--task-file` | JSON/JSONL 任务文件；多条任务依次执行 |
| `--url`、`--prompt` | 直接指定网页和任务；相对 URL 对应本地网页根目录 |
| `--sandbox-directory` | 本地网页根目录；flights 使用 `sandbox/flights` |
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

## 批量实验参数

| 参数 | 说明 |
| --- | --- |
| `--agents`、`--models`、`--tasks` | 逗号分隔，必须是配置中的名称；省略时使用 matrix |
| `--repeats` | 每种组合重复次数；当前配置默认 5 |
| `--config` | 默认 `configs/experiments/default.yaml` |
| `--dry-run` | 输出完整采集参数，不启动 Agent |
| `--report` | 指定计划和返回码报告路径；默认在 `data/experiments/<时间戳与随机后缀>/collection.json` |

当前默认矩阵为 5 个框架 × 4 个模型 × 3 个任务 × 5 次重复，即 300 条采集命令。
`--tasks forums` 是实验任务名，保存的数据网页标签为 `forum`；筛选数据时使用 `--sites forum`。

超时、抓包等 `collect` 选项在实验 YAML 中配置，不直接传给 `experiment`。覆盖顺序：`defaults → task → agent → model → overrides`。
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

## 任务文件

任务文件每行一个 JSON 对象，使用 `url`、`prompt`，也兼容 `web`、`ques`：

```json
{"url": "/01-minimal.html", "prompt": "点击页面上的按钮"}
```

`--url` 与 `--task-file` 不能同时使用；相对 URL 由本地网页服务提供。一次任务执行是一个 run，任务文件有多行时会依次执行。

## 上传与收尾

自动采集结束后会等待 L3 上传进入短暂静默再导出，以免探针的批量上传晚于 Agent 进程退出；Browser-use 还会停止探针并将页面内存快照作为漏传事件的补充，与服务端已收事件按会话和序号去重合并。L3 中的 `browser_snapshot` 字段记录页面快照的 ACK、待上传数和上传错误，便于区分页面未产生事件与上传遗漏。

## Skyvern + Qwen3.5 输出截断与超时

Skyvern 的默认输出额度为 4096 token。若日志反复出现
`LLMOutputTruncatedError`、`finish_reason=length, content=None`，会触发单步重试，
最终可能耗尽 5 次重试或被实验总时限终止。

项目的 Skyvern runner 对 `qwen3.5-*` 默认使用 JSON object 模式，向 vLLM 传递
`chat_template_kwargs.enable_thinking=false`，并将输出额度设为 8192。
这些参数仅作用于 Skyvern 的 Qwen3.5 请求。可通过以下环境变量覆盖：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SKYVERN_LLM_MAX_TOKENS` | Qwen3.5 为 `8192` | 正整数，单次模型输出额度 |
| `SKYVERN_LLM_PROTOCOL` | Qwen3.5 为 `json`，其他模型为 `native` | `json` / `native` / `text` |
| `SKYVERN_QWEN_ENABLE_THINKING` | `false` | Qwen3.5 的 vLLM 思考开关，`true` / `false` |
| `SKYVERN_TASK_TIMEOUT` | `1800` | 默认实验配置中 Skyvern + qwen3.5-9b 的 flights/shop 总时限（秒） |

先各重跑一次验证：

```bash
python -m agent_fingerprint experiment \
  --agents skyvern --models qwen3.5-9b --tasks flights,shop --repeats 1
```

该超时覆盖作用于默认配置的 `experiment`；直接使用 `collect` 时通过 `--timeout 1800` 指定。
没有提高每步重试次数。新参数不能保证网页任务必然成功；如果仍然超时，应检查具体失败动作。
这些运行改变了思考模式、输出额度和超时条件，进行指纹对比时应区分修复前后的采集批次。
