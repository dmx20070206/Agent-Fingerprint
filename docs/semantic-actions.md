# 语义动作：原始事件 E → 高层动作 Z

[文档导航](README.md) · [训练指南](attribution-training.md)

把原始点击、按键、滚动等事件（E）合并成输入、提交、滚动等动作（Z），不调用 LLM。例如，多次按键可以合成一次 `TEXT_ENTRY`。同一命令也提取 98 维统计特征，原始事件保存在结果中。

## 一条命令转换 raw JSON

在仓库根目录运行：

```bash
python -m agent_fingerprint extract \
  --input-file examples/semantic_actions/example_raw.json \
  --output-file data/results/semantic_demo/output.json \
  --debug-output data/results/semantic_demo/debug.txt
```

`--output-file` 省略时，结果写入输入文件旁的 `<原文件名>.semantic.json`。输入可以是带 `events` 的 JSON 对象或事件数组；若所在运行目录有 manifest，会一并读取运行信息。输入文件不会被覆盖。

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `--input-file` | 原始 JSON 对象或事件数组 |
| `--output-file` | 合并原始事件、统计特征和语义动作的输出路径 |
| `--debug-output` | 保存事件识别、动作合并过程，便于排查 |
| `--semantic-config` | JSON 阈值覆盖文件，见下文 |
| `--debug-session` | 只输出指定 session 的调试信息 |
| `--task-id` | 按 run ID 查找已有运行，与 `--input-file` 二选一 |
| `--input-dir`、`--output-dir` | run ID 模式的输入/输出根目录，默认 `data/runs`、`data/results` |

上述示例的 22 个事件输出：

```json
[
  ["TEXT_ENTRY", "TEXT_INPUT"],
  ["SUBMIT", "OTHER"],
  ["SCROLL", "PAGE"],
  ["CLICK", "LINK"]
]
```

原有调用方式保持可用：

```bash
python -m agent_fingerprint extract \
  --task-id RUN_ID --input-dir data/runs --output-dir data/results
```

它继续生成 `data/results/RUN_ID/l3_browser_dynamic.json`（原 v5 格式），并新增同目录 `semantic_actions.json`。`--output-file` 可以单独指定语义文件路径，不改变原统计文件路径。

## 结果先看哪里

| 字段 | 内容 | 用途 |
| --- | --- | --- |
| `schema` | `semantic-actions/v1` | 版本 |
| `raw_trace` | 完整原始对象及 `events` | E、上下文、可追溯性 |
| `existing_statistics` | 完整原 v5 输出，含 98 维向量 | 统计视图；CLI 总是提供 |
| `actions` | 动作类型、目标角色、时间、来源区间、规则、置信度等 | 内部审计 |
| `action_sequence_content` | `[action_type, target_role]` | 仅动作内容 |
| `action_sequence_temporal` | `[action_type, target_role, duration_ms, inter_action_latency_ms]` | 动作内容与时间 |
| `config` | 本次阈值 | 复现 |
| `audit` | 规约记录、候选数量、索引约定 | 调试 |

先看 `action_sequence_content` 是否得到预期动作，再用 `actions` 和调试文本追溯原因。训练时只使用对应的 `action_sequence_*` 字段；URL、按钮文字、坐标和审计信息不进入语义模型。

## 新采集建议

需要更完整的控件、滚动和输入信息时，采集时加 `--collector semantic/v1`。例如人工操作：

```bash
python -m agent_fingerprint collect \
  --manual --url /11-mouse-click.html --collector semantic/v1
```

已有 legacy/v2 数据也能解析，但缺失的 DOM 结构、滚动起点或悬停证据无法补回。某动作未被识别，可能是证据不足；可通过 `--debug-output` 查看原因。
增强采集会影响新数据的观测内容，同一份原始事件的 v5 统计计算口径不变。

## 调整阈值与深入排查

一般使用默认阈值即可。需要调整时，创建 JSON 文件（例如 `{"T_SCROLL_GAP_MS": 350}`），通过 `--semantic-config` 传入；时间阈值单位为毫秒。该参数只作用于本次 extract，不会传给 dataset；dataset 使用默认语义配置。

Python API、全部阈值、识别与合并规则、探针扩展和测试命令见[语义解析实现](reference/semantic-details.md)。用于训练时，继续阅读[数据集构建](data-contract.md)和[训练指南](attribution-training.md)。
