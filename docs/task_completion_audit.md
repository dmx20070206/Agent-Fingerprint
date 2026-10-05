# 五个 Agent 框架的完成判定审查

`data/runs/final` 的 96 个目录已另行完成逐项审计，见 [final 测试分类总表](final_task_audit.md)。该表包含每个目录的分类、原因和独立证据记录；final 数据的具体分类以该表为准。

审查日期：2026-10-03。结论：存在成功后未停止、正常结束却标失败、结果未知却标成功，以及填错信息仍判成功四类问题。不能把这些情况统一归为任务执行能力不足。

## 范围与证据

递归扫描 `data/runs` 下全部 521 份 manifest，核查 Agent-E 保存的 SSE 响应、Browser-use 的最终 judgement 与 L4 页面观察、WebVoyager 失败记录的交互日志，并抽查 AutoGen、Skyvern 的失败原因和成功输出。

| 框架 | 目录记录 | success | failed |
|---|---:|---:|---:|
| Agent-E | 86 | 61 | 25 |
| AutoGen | 72 | 55 | 17 |
| Browser-use | 202 | 84 | 118 |
| Skyvern | 68 | 54 | 14 |
| WebVoyager | 93 | 61 | 32 |

这里是目录数，包含 `final` 归档副本，不能当作独立实验次数；按框架与 `started_at` 合并得到 471 组。完整目录清单及 Browser-use 页面证据位置保存于 [审查数据](../data/results/completion_audit_2026-10-03.json)。历史运行可能使用不同版本，当前源码用于解释可复现的问题，不能替代历史执行证据。

## 1. WebVoyager：已确认成功后未停止

代表运行：[2026-10-03/webvoyager_deepseek_flights](../data/runs/2026-10-03/webvoyager_deepseek_flights/manifest.json)。

- 第 90 个动作 `Type [2]; 854` 自动按 Enter，付款表单随即提交。
- [screenshot91.png](../data/runs/2026-10-03/webvoyager_deepseek_flights/artifacts/framework/20261003_18_26_13/tasksingle/screenshot91.png) 已显示预订成功。
- Agent 仍认为位于付款页，继续点击和输入，触发 `list index out of range`；耗尽 100 步后退出码为 3。
- `lib/WebVoyager/run.py` 通过 `ANSWER` 或 `page_reports_success()` 结束；后者读取 `body.dataset.taskStatus`。当时航班页面没有发布该标记。

此前已补齐航班页面 `pending → passed` 协议，真实浏览器验证按钮与 Enter 提交均能被现有 WebVoyager 检测函数识别。历史记录未被修改，尚未重跑完整 LLM 任务。其他失败中常见的下拉框循环、模型请求错误和中断不能据此判为同一问题。

## 2. Browser-use：已确认完成证据没有传给最终评审

118 条失败记录中，99 条的 L4 `<browser_state>` 明确包含预订成功文字，或购物车内三个目标商品及 `$98.97` 总价。这是页面观察证据，而不仅是最终回答；它仍不等于每个任务字段都已严格核验。

分布：2026-09-21 为 1 条，2026-09-22 为 88 条，2026-10-03 为 5 条，final 为 5 条。

代表记录：

- [2026-09-22/browseruse_deepseek_flights](../data/runs/2026-09-22/browseruse_deepseek_flights/manifest.json)：页面观察包含 `Thank you, Joe Jones!` 和预订成功文字；随后重复调用三次 `verify_page_status`，均返回 `taskStatus=None, resultText=None`，最终 `done(success=true)`。评审输入却是动作轨迹与“0 screenshots”，未包含完整页面观察，评审以证据不足否决。
- [2026-10-03/browseruse_deepseek_shop](../data/runs/2026-10-03/browseruse_deepseek_shop/logs/agent.stdout.log)：购物车观察包含 Budget Membrane Keyboard、Basic USB Keyboard、RGB Membrane Keyboard、税额和总价；评审仍以没有截图和显式状态为由返回 `verdict=false`。同日 `_2` 至 `_5` 有同类情况。

`adapters/browseruse_adapter.py` 在 `history.is_successful()` 之后再读取页面状态；如果没有明确 `passed`，会用 `judgement.verdict` 覆盖结果。这类运行通常已经正常调用 done，主要问题是**结束后被误判失败**；部分记录还多做了重复验证。页面标记的兜底读取发生在 `agent.run()` 返回后，不是每步都自动停止。

航班页面协议修复可提供后续运行的明确证据。购物任务当前仍缺少对应协议，且“打开购物车”本身不应无条件代表完成任意购物任务，需要核对过滤条件、商品和任务要求。

## 3. Agent-E：已确认终止事件被过滤，未知结果被当成成功

两个缺陷串联：

1. `lib/Agent-E/ae/server/api_routes.py` 会发出 `transaction_done` 或 `max_turns_reached`。
2. `lib/Agent-E/ae/core/playwright_manager.py::notify_user()` 在调用通知管理器之前，先按 UI 消息白名单过滤；两种 overlay 配置都不允许 DONE、MAX_TURNS_REACHED、ERROR。终止事件因而不会进入 SSE。提取当前函数并用最小桩执行，确认三类事件在两种配置下均发送 0 条。
3. `adapters/agente_adapter.py` 找不到终止事件时写入 `task_success=None`；`adapters/base_adapter.py::AgentResult.success` 使用 `returncode == 0 and task_success is not False`，将未知结果算作成功。

86 份目录记录未检出上述结构化终止事件。61 条 success 中，25 条响应最后停在 `step`，对应 18 组不同 started_at；这些只能标为缺少完成证据，不能全部断言任务失败。

明确反例：

- [2026-10-03/agente_gemini_flights/response.txt](../data/runs/2026-10-03/agente_gemini_flights/artifacts/framework/response.txt)：最终 answer 明说工具持续通信异常、任务无法完成；manifest 却为 success。
- [2026-09-21/agente_claude_mouse_move](../data/runs/2026-09-21/agente_claude_mouse_move/manifest.json)：最终回答明确“任务无法完成”，仍为 success。
- [2026-10-03/agente_chat_gpt_flights](../data/runs/2026-10-03/agente_chat_gpt_flights/artifacts/framework/response.txt)：响应最后只是要求点击 Book Flight，没有最终确认，外层仍标 success。不能仅凭这条步骤指令推断页面是否成功。

另一个语义风险：服务端 `is_terminating_message()` 只检查 `terminate == yes`，它表示对话结束，不保证用户目标成功。即使修复事件过滤，也不能直接把所有 transaction_done 当作任务成功。

## 4. AutoGen：成功条件过宽，停止条件与结果判断不一致

当前视觉分支用 `TASK_COMPLETE` 或 `successfully booked` 停止，而最后通过 `_SUCCESS_KEYWORDS` 对所有 Agent 消息做子串匹配判成功；列表还含较宽的“通过”。文本分支主要依赖 `TASK_COMPLETE` 结束。这种设计可能把提及目标、否定句或中途成功当作最终成功，且结果判断为真不保证及时触发停止。

已确认的错误完成例子：final 中 `autogen_gemini_flights_2` 至 `_5`，任务要求 Joe Jones、安全码 854，但输出显示输入 123、确认页为 `Thank you, John Doe!`，仍记录 success。见 [示例 manifest](../data/runs/final/flights/autogen/gemini/autogen_gemini_flights_4/manifest.json)。成功页面触发了停止，但任务字段没有满足要求。

旧记录也有可疑尾部：[autogen_chat_gpt_keyboard](../data/runs/2026-09-21/autogen_chat_gpt_keyboard/logs/agent.stdout.log)、mouse_move 的最终输出分别是“请提供具体请求”“请提供下一项具体请求”，两者均有 50 次 LLM request_start，仍被记录成功。这支持停止判定需要复查，但旧记录缺少完整对话，不能精确断言首次成功发生在哪一步或是否确实跑满配置上限。

2026-10-03 的 Claude 航班超时样本，末尾观察仍在航班详情/旅客信息页，并持续尝试处理 carry-on 控件；这属于未完成操作，不是已成功却未停止。

## 5. Skyvern：未发现同类已确认反例

适配器读取服务返回的 `status`，将 completed/succeeded/success/passed 映射为成功，保留 failure_reason。检查到的失败主要是目标元素不能定位、不支持所需输入方式、步数耗尽、请求解析错误或运行中断；不足以证明它们已完成却没有停下。

与其他框架相比，当前结果直接采用服务端状态，未与本地页面标记独立交叉校验。这属于审查限制/改进点，不能据此认定存在误判；没有逐帧审查全部录屏。

## 建议顺序

1. 修复 Agent-E 终止事件传输，并将未知结果与成功分开；再区分会话结束和任务完成。
2. 给 Browser-use 的最终评审传递实际页面证据，分别保存 Agent 自报、页面状态、评审结论，避免无证据的覆盖。
3. AutoGen 停止与结果判断共用明确协议，避免宽泛关键词；将流程完成与参数符合任务要求分开验证。
4. 在采集时记录第一次完成证据的时间/步骤、最后动作时间/步骤和退出原因，以便可靠统计成功后的多余动作。

本次仅审查并保存报告，未批量改写历史标签或修改框架逻辑。
