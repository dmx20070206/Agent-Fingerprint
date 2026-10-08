# 语义解析实现与限制

[文档导航](../README.md) · [使用指南](../semantic-actions.md)

本页用于调试解析器和核对规则。所有命令均在仓库根目录执行；日常转换只需使用指南中的 `extract` 命令。

## 输出字段与事件索引

只将明确白名单生成的两个 `action_sequence_*` 字段分别提供给对应模型。`actions`、`raw_trace`、`audit`、置信度、目标 ID、按钮文字、URL、坐标和像素位移均不属于内容序列的模型输入。可选的粗粒度滚动方向、历史方向和导航 outcome 目前仅放在 `actions` 中。

来源索引基于输入 `raw_trace.events`，从 0 开始、**两端包含**：

```python
events[action["source_event_start_index"]:action["source_event_end_index"] + 1]
```

解析不重新排序事件，也不删除原始 pointer/mouse 配对事件；配对信息只标注在内部规范化事件上。吸收导航结果时，原动作区间不延长到新页面，规约审计单独记录导航事件索引。

## Python API

```python
import json
from pathlib import Path

from agent_fingerprint.features.l3_browser_dynamic import format_l3
from agent_fingerprint.semantic_actions import parse_semantic_actions, SemanticActionConfig

raw = json.loads(Path("examples/semantic_actions/example_raw.json").read_text())
result = parse_semantic_actions(raw, SemanticActionConfig(T_SCROLL_GAP_MS=300))
document = result.to_dict(existing_statistics=format_l3(raw))
text = result.to_json(existing_statistics=format_l3(raw))
print(result.debug_trace())
print(result.debug_trace(session_id="SESSION_ID"))
```

也可调用 `abstract_events(raw, config=None, existing_statistics=None)` 直接得到字典，以及 `debug_trace(raw, config=None, session_id=None)` 直接得到审计文本。低层 API 不强制重复计算 v5，调用者可传入已有统计结果。

## 解析流程与规则

```text
Raw E → EventNormalizer → independent recognizers → candidate intervals
      → SemanticReducer → TimingAnnotator → Z + model projections
```

- `targets.py`：优先使用 `composed_path` / `composedPath`，其次使用 `ancestors`、`parent` / `parentElement`；选择最近的语义控件。`option` 提升到所属 select，嵌套 span/svg 提升到 button。滚动始终保留实际滚动容器的坐标系。
- `normalize.py`：复用原项目的有限数值解析与 pointer/mouse 配对函数。只给配对事件加内部标记，不删除任何事件。补充严格的 document、session、monitor_stop、导航、卸载和时钟回退边界。新采集器通过 `semantic_event_type` 保留旧探针已归一化的 pointercancel 等原类型。
- `recognizers.py`：独立识别九种动作。编辑从真实编辑证据开始；点击从 down 开始或退化为 click 的零时长；选择/切换要求变化证据；提交吸收 Enter 触发；滚动依据坐标变化形成 burst；悬停必须具有外部进入证据和停留时间。
- `reducer.py`：输入控件 click、选择控件 click、切换控件 click、提交按钮 click 被相应高层行为吸收；真实文字编辑仍然保留。短时间转为同目标 click 的 hover 被吸收。CLICK(LINK/BUTTON)、SUBMIT、HISTORY_NAV 引起的页面变化记为 outcome。不同表单、不同目标和中间出现的独立动作阻止不可靠的吸收。
- `pipeline.py`：保留候选、规约记录和最终结果，生成明确的内容／时序白名单与 debug trace。

时间计算优先使用两个端点都有的 `epoch_ms`；否则只有同一 session/document/block 内可使用 `monotonic_ms`。不会将一个端点的 epoch 与另一个端点的 monotonic 相减。任一时钟回退都会切断动作；跨硬边界的 inter-action latency 设为 `null`。缺少可比较的时间时保留动作，时间设为 `null`。

旧探针的 `session_id` 实际是每次文档安装的 ID。无显式 document ID 时，parser 按 monitor_start / session 变化构造文档 ID；跨这种旧页面安装 ID 的导航因果判断只使用 epoch 时间。显式 document/session 元数据则严格保留 session 隔离。初次 monitor_start 不产生导航动作。

## 集中阈值

通过 `--semantic-config config.json` 传入任意子集，未知参数报错，所有值必须为有限非负数：

```json
{
  "T_SCROLL_GAP_MS": 350,
  "T_HOVER_MIN_MS": 800,
  "T_HOVER_CLICK_MS": 400,
  "T_ACTIVATION_MAX_GAP_MS": 1500,
  "T_TEXT_ENTRY_IDLE_MS": 30000,
  "T_NAV_CAUSAL_WINDOW_MS": 5000,
  "T_CONTROL_CHANGE_MS": 1500
}
```

这些是首版默认值，尚未通过人工标注数据集调优。普通数秒键间停顿不会切开 TEXT_ENTRY；滚动停顿超过阈值则保留为独立动作。

## 采集缺失项与扩展接入

旧探针已经提供 session、双时钟、基本 target 信息、popstate/pushState/replaceState 导航、页面滚动。主要缺少：

| 缺失项 | 增强探针的补充 |
| --- | --- |
| composedPath、祖先路径、relatedTarget 的祖先路径 | 结构化路径及每文档 WeakMap node ID |
| 独立 document ID、contenteditable、form/submitter 关联 | document_id、目标结构字段、form_id、submitter_id |
| checked / selection / value 的变化证据 | checked、selected_index、checked_changed、value_changed |
| beforeinput / IME composition | 增加四类编辑事件 |
| scrollend、容器滚动、滚动初始状态 | 实际目标坐标、前一坐标、scroll_changed、结束事件 |
| 被旧探针重命名的 pointercancel 等类型 | semantic_event_type，保留原统计使用的 type |

`collector.py` 使用基础探针明确的 `AF_MONITOR_EXTENSION_API:1` 插件接口，拼接增强插件与基础脚本，不替换原实现片段。
默认使用 legacy/v2，增强采集显式启用：

```bash
python -m agent_fingerprint collect --manual --url /11-mouse-click.html --collector semantic/v1
```

也可以生成独立探针：

```bash
python -m agent_fingerprint extract --write-semantic-probe /tmp/semantic_monitor.js
```

Python 服务入口支持 `create_server(..., collector_version="semantic/v1")`；PipelineRunner 使用同名参数。
manifest 记录采集器版本及脚本校验和。每个服务器实例独立选择版本，不需要修改全局 handler 类。

扩展仅在内存中比较文本，不上传实际 value 或其哈希；原探针自身已有的 key / text 等字段仍按原策略保留。新事件和更完整的滚动观测会影响**新采集数据**的统计数值，但相同 E 的 v5 计算结果完全不变。旧 JSON 不会被补写或重采样。

## 保守降级与限制

- 没有祖先信息时不会从 CSS selector、class、文本或 URL 猜测 button/link；不能可靠定位所属 select 的 option 保持 OTHER。
- 缺少 node ID / DOM ID / 路径时，匿名目标只能按已有结构暂时归一；无法保证区分结构完全相同的两个控件。
- 只有首个滚动位置、没有初始位置或显式变化标志时，该位置作为 baseline，不假设起点为 0。旧探针已经丢失的容器滚动和中间事件无法恢复。
- 明确 `*_changed=false` 不视为成功操作；SELECT/TOGGLE 的原生 change 事件在状态不可用时作为变化证据。单独 click 始终不足以断言成功。`beforeinput` / paste / composition 没有后续 input 时使用较低置信度。
- HOVER 要求显式 relatedTarget（允许 null 表示从页面外进入）或其路径。旧数据缺少该字段时不输出 HOVER，也不从整段 session 的结束时间臆造停留。
- 未维护历史索引时只有 HISTORY_NAV；仅在观测到前后 history_index 时标注 BACK/FORWARD。
- 这是一组可解释规则，不声称已完全去除 framework 信息；内容序列与时序视图应分别评估。

## 测试

根仓库默认测试发现已包含语义层，也可以单独执行：

```bash
python -m pytest -q tests/semantic_actions tests/test_l3_browser_dynamic.py tests/test_probes.py
```

Python parser 无新增依赖；测试依赖 pytest。collector 测试用 Node 执行组合后的真实脚本，并使用最小 DOM 事件桩验证输出和隐私字段；它不是完整真实浏览器测试。`tests/fixtures/v5_regression.json` 保存从修改前 Git 版本生成的固定统计结果。
