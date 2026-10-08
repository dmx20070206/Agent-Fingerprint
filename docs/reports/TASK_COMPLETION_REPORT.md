# TASK.md 完成报告

[文档导航](../README.md) · **历史交付记录**：保留当次任务的实现和验证结果。当前入口、参数及示例见[项目首页](../../README.md)。

完成日期：2026-10-06。

已实现独立、确定性、无需 LLM 的 Semantic Action Abstraction。入口仍是 `analysis/l3_browser_dynamic.py`，其余新增实现、采集扩展、测试、示例与本报告均位于 `lib/semantic_actions/`。没有修改 `TASK.md`、其他既有模块或 `lib/` 中原有第三方项目。

## 直接使用

```bash
python analysis/l3_browser_dynamic.py \
  --input-file path/to/raw.json \
  --output-file path/to/semantic_actions.json \
  --debug-output path/to/semantic_debug.txt
```

输出同时包含完整 `raw_trace`、语义 `actions`、`action_sequence_content`、`action_sequence_temporal` 和原 v5 `existing_statistics`（98 维）。原始文件不会被覆盖；输入输出同路径、debug 或 probe 输出与输入冲突时会报错。

原来的 `--task-id / --input-dir / --output-dir` 用法继续可用：原位置仍输出原格式统计文件，旁边增加 `semantic_actions.json`。

可直接查看本目录的 [example_raw.json](example_raw.json)、[example_output.json](example_output.json) 和 [example_debug.txt](example_debug.txt)。示例的 22 个原始事件规约为：

```text
TEXT_ENTRY(TEXT_INPUT)
SUBMIT(OTHER)
SCROLL(PAGE)
CLICK(LINK)
```

## 完成内容

| 交付项 | 实现 |
| --- | --- |
| Semantic Action schema、候选区间、集中配置 | `schema.py`：NormalizedEvent、CandidateAction、SemanticAction、SemanticActionConfig |
| 八类 canonical target role、目标提升 | `targets.py`：composedPath 优先，祖先／parent 回退，嵌套按钮与 option 提升 |
| Event Normalizer | `normalize.py`：原始索引、双时钟、文档／会话／导航／回退边界、状态证据、内部 pointer/mouse 配对标记 |
| 九类动作识别器 | `recognizers.py`：TEXT_ENTRY、CLICK、SCROLL、SELECT、TOGGLE、SUBMIT、HOVER、NAVIGATE、HISTORY_NAV |
| Semantic Reducer | `reducer.py`：机械 click 吸收、hover 吸收、提交触发吸收、导航 outcome 规约、干预动作阻断 |
| Timing Annotator | `reducer.py`：独立时间视图、同源时钟计算、跨硬边界 gap=null |
| E → Z 主函数、JSON serialization | `pipeline.py` / `__init__.py`：parse_semantic_actions、abstract_events、to_dict、to_json |
| Debug trace | 原始 E → 候选 C → 吸收规则 → 最终 Z，支持 session 过滤 |
| 启动脚本兼容 | 原 `analysis/l3_browser_dynamic.py` 的 main 委托 `cli.py`，统计函数保持原样 |
| 采集缺失字段补充 | `collector.py` + `collector_extension.js`，组合原采集器生成增强版本 |
| 单元／集成测试 | `tests/`，包含固定 v5 回归快照、Node 采集执行测试、实际 HTTP 沙盒接入测试 |
| 使用与限制说明 | [README.md](README.md)，包含配置、Python API、字段说明及采集接入方式 |

## 主要规约与正确性约束

- 键入、粘贴、程序化 input 统一为 TEXT_ENTRY；focus 不产生编辑动作。文本动作从首个编辑证据计时，获取焦点的 click 只扩展来源区间。真实编辑在 SUBMIT 前保留。
- CLICK 使用 down → click 区间；click-only 仍产生动作。pointer/mouse 配对不重复计动作，真实连续 mousemove 不删除。取消 activation 后不会沿用旧 down。
- SELECT / TOGGLE 需要变化或 change 证据。单独点击、明确状态未改变均保留 CLICK；input/change 确认链只形成一个动作。
- 提交按钮和 Enter 触发规约为统一的 SUBMIT(OTHER)。已知 form/submitter 不匹配时不吸收无关触发。
- SCROLL 依据实际位置变化，按 scrollend、时间间隔或新交互结束 burst。wheel-only 不产出 SCROLL，不将容器坐标与页面坐标混合。
- HOVER 要求可观察的外部进入及足够停留时间；同控件内部移动不重新开始 hover，短时间转为 click 时吸收。
- CLICK(LINK/BUTTON)、SUBMIT、HISTORY_NAV 引起的新页面保留为原动作 outcome；无可解释前因的导航才产生 NAVIGATE。未观测历史索引时不猜测 BACK/FORWARD。
- session/document 变化、导航、monitor_stop、beforeunload/pagehide、任一时钟回退均切断未完成动作。来源区间不跨硬边界；导航结果的审计索引单独记录。
- 内容与时间序列使用显式字段白名单。DOM ID、selector、文本、URL、坐标、像素距离、置信度与规则名不进入 Decision Content。

## 与现有项目的复用及兼容性

编码前检查了 `l3_browser_dynamic.py`、`episode_features.py`、`feature_schema.py`、`_common.py`、`probes/inject_monitor.js`、沙盒注入路径及实际 raw 数据。

复用现有数值解析、pointer/mouse 配对、run 路径解析、manifest metadata 加载和统计计算。采用 episode 特征已有的 epoch 优先、成对 monotonic 回退原则；由于原统计 segmenter 没有完整的 document/monitor_stop 边界，语义 normalizer 单独补齐边界，不改动原统计逻辑。旧探针每文档生成的 session ID 也有明确兼容处理。

原统计部分仍是 67 个基础浏览器特征加 31 个 episode 特征，共 98 维。`format_l3()` 函数正文与特征定义文件均未修改。固定回归 fixture 的预期输出由修改前的 Git 版本生成，基线提交记录在 [verification.json](verification.json) 中。

## 采集缺失项与处理

原采集器缺少 composedPath / 祖先 / relatedTarget 路径、稳定 node ID、独立 document ID、contenteditable、form/submitter 关联、checked/value/selection 变化、IME 事件、scrollend，以及可靠的容器滚动坐标与 baseline。

为遵守“其余所有文件写入 lib 新目录”的要求，没有直接改写原 probe 或 server。提供组合式增强采集器：

```bash
python analysis/l3_browser_dynamic.py \
  --write-semantic-probe lib/semantic_actions/generated_monitor.js
```

生成脚本复用原 probe 的上传、ACK、停止和导航实现，仅替换必要采集钩子。通过现有 `SandboxRequestHandler.monitor_path` 接入；完整例子见 [README.md](README.md#采集缺失项与扩展接入)。此接入方式已通过实际 HTTP 服务测试。默认采集流程没有被自动切换。

文本仅在浏览器内存中比较，不额外上传 actual value 或文本哈希。增强采集器在 DOMContentLoaded 后补拍控件及滚动容器 baseline。新增采集字段和事件只影响以后采集的数据，无法恢复旧 JSON 已丢失的信息。

## 验证结果

运行：

```bash
python -m pytest -q lib/semantic_actions/tests test/test_l3_browser_dynamic.py test/test_probes.py
```

结果：**105 passed**，其中新增测试 80 项，原 L3／probe 测试 25 项。覆盖 TASK.md 列出的全部 25 个场景，另包含以下回归和边界：

- 等长文本替换、IME、普通键间停顿、输入目标切换、程序化 select/toggle 中断编辑。
- 匿名／嵌套目标、同控件内部 hover、pointercancel、不同目标／页面不去重。
- 两次独立 toggle、input/change 确认、不同表单提交、无时间戳时的保守吸收。
- epoch 优先、混合缺失端点、epoch 递增但 monotonic 回退、跨页面 gap=null。
- 不可变 raw、原始索引不压缩、可复现结果、模型投影白名单。
- 文件模式／原 task-id 模式 CLI、原文件覆盖保护、v5 固定快照。
- 组合后的采集器语法与执行、敏感 value 不输出、采集源码钩子漂移报错、HTTP 沙盒接入。

对仓库现存 **222 份 raw JSON** 进行了额外回归：

| 项目 | 结果 |
| --- | --- |
| 原始事件 | 67,419 |
| 最终语义动作 | 5,144 |
| 与修改前 v5 完整结果逐文件比较 | 222/222 完全一致 |
| 所有最终动作的来源区间 | 均位于单一硬边界区间内 |
| duration / gap | 均为非负值或 null |

其中 TEXT_ENTRY 933、SUBMIT 499、SELECT 471、CLICK 1,619、SCROLL 1,453、NAVIGATE 1、TOGGLE 168。该数据集未输出 HOVER/HISTORY_NAV；相关规则由专门测试覆盖，不将缺少旧采集证据解释为实际没有发生这些行为。汇总保存在 [verification.json](verification.json)。这些结果验证兼容性与结构约束，不等同于人工标注的语义识别准确率。

已实际执行单文件 CLI：一个真实 run 的 53 个事件转换为 9 个动作，并包含 98 维统计；仓库中的独立示例得到预期的 4 个动作。Python 编译、增强 probe 的 `node --check` 和 `git diff --check` 均通过。

## 尚存限制与全仓库测试情况

完整运行 `python -m pytest -q test lib/semantic_actions/tests` 在测试收集阶段遇到三项现有环境／仓库阻碍：

1. `test_classification.py` 缺少 `matplotlib`。
2. `test_migrate_runs.py` 引用的 `scripts.migrate_runs_v1_to_v2` 不存在。
3. `test_orchestrator.py` 缺少 `dotenv`。

因此没有声称全仓库测试全部通过，也没有为本任务修改无关模块或安装依赖。新增 collector 执行测试使用 Node 的最小 DOM 桩，加上实际 HTTP 沙盒测试；当前环境没有 Playwright，未进行完整真实浏览器测试。

旧 raw 没有祖先时无法可靠提升嵌套目标；没有节点标识时无法保证区分完全同结构控件；没有滚动 baseline 时首个位置只作为 baseline；缺少 relatedTarget 时不输出 HOVER。新插入且尚未观测过的滚动容器同样不能凭空恢复初始位置。配置阈值为首版默认值，尚未经过人工标注数据集校准。
