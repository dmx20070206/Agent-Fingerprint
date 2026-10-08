# 语义动作层原始需求（历史设计）

[文档导航](../README.md) · 本文保留原始需求，出现的 `analysis/`、`lib/` 等路径属于旧布局。当前实现位于 `src/agent_fingerprint/semantic_actions/`；使用命令见[语义动作指南](../semantic-actions.md)。

请为现有浏览器行为指纹项目实现一个新的“Semantic Action Abstraction”层，将底层浏览器事件序列 E 转换为高层语义动作序列 Z。
最终效果是，运行一个脚本后，可以将一个raw的json文件变成一个记录高层语义动作序列 Z 的新 json。
启动脚本仍然写入analysis/l3_browser_dynamic.py中，之前的60+个维度仍然提取，其余所有文件写入lib，新建一个文件夹

不要修改现有 v5 的 98 维统计特征定义和计算逻辑。新的语义动作层应作为独立模块存在，原始事件 E 和现有统计特征全部保留。

==================================================
1. 目标与设计原则
==================================================

现有底层事件序列定义为：

E = [e_1, e_2, ..., e_N]

其中底层事件包括但不限于：

focus
blur
keydown
keyup
input
change
paste
click
dblclick
contextmenu
mousedown
mouseup
pointerdown
pointerup
pointercancel
pointerover
pointerout
wheel
scroll
scrollend
touchstart
touchend
touchmove
selectionchange
submit
reset
popstate
navigation / navigate
monitor_start / monitor_stop
beforeunload / pagehide
等。

我们希望在 E 的基础上构造高层语义动作序列：

Z = [z_1, z_2, ..., z_M], M << N

每个语义动作表示“Agent 在语义上做了什么”，而不是“Agent framework 如何执行这个动作”。

核心原则：

E 保留 HOW：
    一个动作具体如何被 framework 执行。

Z 尽量保留 WHAT：
    Agent 在语义上执行了什么动作。

例如：

Framework A:
focus
keydown
input
keyup
keydown
input
keyup
change

Framework B:
focus
paste
input
change

Framework C:
input
change

三者在 Z 中都应该归一化为：

TEXT_ENTRY(TEXT_INPUT)

不能因为 paste / keydown / change 等 implementation difference，在 Z 中形成不同的动作序列。

Semantic abstraction 的主要目标之一，就是尽可能消除 Agent framework 的机械执行差异，使 Z 更接近 decision / policy 层。

注意：

Semantic Action Abstraction 不能使用 LLM 来完成。
必须使用确定性的规则、状态机和事件区间解析逻辑。
否则会引入额外模型偏差，且无法稳定复现。

==================================================
2. Z 的最终数据结构
==================================================

语义动作定义为：

z_i = (
    action_type,
    target_role,
    duration,
    inter_action_latency
)

其中：

duration_i =
    t_i_end - t_i_start

inter_action_latency_i =
    t_(i+1)_start - t_i_end

但是内部实现时，必须保留更多 metadata 用于 debug 和 audit。

建议内部结构：

SemanticAction {
    action_type
    target_role

    start_time_ms
    end_time_ms
    duration_ms

    inter_action_latency_ms

    source_event_start_index
    source_event_end_index

    canonical_target_id

    document_id
    session_id

    recognition_rule
    confidence
}

其中：

source_event_start_index / source_event_end_index
canonical_target_id
recognition_rule
confidence

仅用于内部调试、验证和可解释性。

这些字段不能作为 Decision Model 的输入。

真正给 Decision Model 的 Z_content 应主要包含：

action_type
target_role

必要时可以包含粗粒度 subtype / observable outcome。

Timing 单独作为 Temporal Orchestration View。

==================================================
3. Action Type 定义
==================================================

第一版支持以下语义动作：

TEXT_ENTRY
    向文本控件输入或修改文本。

CLICK
    激活普通可点击元素。

SCROLL
    一次独立的语义滚动动作。

SELECT
    从 select / combobox / listbox 中选择选项。

TOGGLE
    修改 checkbox / radio / switch 状态。

SUBMIT
    提交表单或提交当前输入。

HOVER
    明确、持续的悬停交互。
    必须非常保守识别。

NAVIGATE
    没有被前置页面交互解释的直接导航。

HISTORY_NAV
    浏览器历史导航，例如 back / forward。

第一版先不要加入过多 task-specific action，例如：

SEARCH_PRODUCT
COMPARE_PRODUCT
BUY_PRODUCT

这些会使 Z 过于依赖具体网页和任务。

Semantic action 应尽可能 page-agnostic。

==================================================
4. Target Role 定义
==================================================

请实现 canonical target role normalization。

Target Role 只能使用以下粗粒度类别：

LINK
BUTTON
TEXT_INPUT
SELECT
TOGGLE
CONTAINER
PAGE
OTHER

具体定义：

LINK
    HTML <a> 且具有 href，
    或 accessibility role="link"。

BUTTON
    <button>
    <input type="button">
    <input type="submit">
    <input type="reset">
    <input type="image">
    role="button"
    以及语义上明确属于 button 的元素。

TEXT_INPUT
    <textarea>
    <input type="text">
    <input type="search">
    <input type="email">
    <input type="url">
    <input type="tel">
    <input type="password">
    <input type="number">
    contenteditable 元素
    role="textbox"
    role="searchbox"

SELECT
    <select>
    role="combobox"
    role="listbox"
    <option> 应向上归一化到所属 select。

TOGGLE
    <input type="checkbox">
    <input type="radio">
    role="checkbox"
    role="radio"
    role="switch"

CONTAINER
    非上述语义控件，但可作为交互或滚动区域的页面元素。
    如：
    div
    section
    main
    scrollable panel
    list container
    等。

PAGE
    window
    document
    html
    body
    或页面级滚动、页面级导航。

OTHER
    无法可靠分类到其他类别的元素。

==================================================
5. Canonical Target Lifting
==================================================

不要直接使用 event.target.tagName 作为最终目标。

必须实现 canonical semantic target lifting。

例如：

<button>
    <span>
        <svg>
        </svg>
    </span>
</button>

用户可能实际点击：

svg

但语义目标应该被提升为：

BUTTON

优先使用：

event.composedPath()

如果数据中没有 composedPath，则沿 parent / ancestor 信息寻找最近的语义元素。

例如：

raw target = SVG
ancestor = SPAN
ancestor = BUTTON

最终：

canonical_target = BUTTON
target_role = BUTTON

canonical_target_id 可以内部保存，用于判断多个底层事件是否属于同一个语义动作。

但是：

DOM id
class
CSS selector
element text
具体 URL
按钮文本

不能作为 Decision Model 输入。

==================================================
6. 总体架构：像一个小型编译器
==================================================

不要写成一个巨大的 if-else。

整体 pipeline 应为：

Raw Events E
    ↓
Event Normalizer / Lexer
    ↓
Parallel Action Recognizers
    ↓
Candidate Actions
    ↓
Semantic Reducer
    ↓
Timing Annotator
    ↓
Final Semantic Sequence Z

即：

E
→ E_normalized
→ candidate action intervals
→ semantic reduction
→ Z

建议拆分模块：

EventNormalizer
TextEntryRecognizer
ActivationRecognizer
ScrollRecognizer
SelectRecognizer
ToggleRecognizer
SubmitRecognizer
HoverRecognizer
NavigationRecognizer
SemanticReducer
TimingAnnotator

每个 recognizer 负责识别候选动作区间。

SemanticReducer 再负责处理：

重叠
优先级
吸收关系
执行细节消除

==================================================
7. Event Normalizer
==================================================

所有原始事件首先转换为统一结构：

NormalizedEvent {
    index

    event_type

    epoch_ms
    monotonic_ms

    session_id
    document_id

    canonical_target_id
    target_role

    x
    y

    button

    key
    key_category

    value_changed
    checked_changed

    scroll_x
    scroll_y

    related_target_id

    url

    raw_event
}

允许字段为空。

时间规则沿用当前项目：

优先 epoch_ms。
缺失时，只允许在同 session / document 范围内使用 monotonic_ms。
时间回退视为 hard boundary。
不能跨 document 拼接本地 monotonic clock。

==================================================
8. Pointer / Mouse 去重
==================================================

保留原始 E 不变。

但 semantic parser 内部需要识别 pointer/mouse duplicated event。

例如：

pointerdown
mousedown
pointerup
mouseup
click

它们不代表 5 个 semantic action。

pointerdown + mousedown
pointerup + mouseup

可以视为同一个 activation cycle 中的重复底层证据。

不得误删真正的连续 mousemove。

==================================================
9. Hard Boundary
==================================================

发生以下条件时，所有未完成 candidate action 必须 flush：

session change
document change
monitor_stop
navigation boundary
beforeunload
pagehide
时钟回退

任何 semantic action 不允许跨 document。

特别是：

TEXT_ENTRY
SCROLL
HOVER

不能跨页面。

==================================================
10. TEXT_ENTRY Recognizer
==================================================

TEXT_ENTRY 代表：

“向一个 TEXT_INPUT 进行了实际的文本编辑”。

focus 本身不能产生 TEXT_ENTRY。

真正启动 TEXT_ENTRY 必须看到文本编辑证据，例如：

input
paste
beforeinput
compositionstart / compositionupdate / compositionend
或者可信的 printable keydown + 后续 input

如果项目目前没有采集：

beforeinput
compositionstart
compositionupdate
compositionend

建议新增采集。

尤其为了兼容中文、日文等 IME 输入。

典型底层序列：

focus
keydown
input
keyup
keydown
input
keyup
change

→

TEXT_ENTRY(TEXT_INPUT)

另一个序列：

focus
paste
input
change

→

TEXT_ENTRY(TEXT_INPUT)

另一个序列：

input
change

→

TEXT_ENTRY(TEXT_INPUT)

三者语义结果必须一致。

TEXT_ENTRY state machine 可设计为：

IDLE
↓
检测到真实编辑证据
EDITING
↓
blur / submit / navigation / target switch / 明确结束
END

同一个 target 上连续的 keydown/input/keyup/change，应尽可能归并成一个 TEXT_ENTRY。

TEXT_ENTRY 的 start_time：

优先使用第一次真正产生编辑行为的时间。

不要使用早期 mousemove 时间。

可选择是否把获取焦点的 click 纳入 source span，但不能让它在最终 Z 中额外产生一个 CLICK。

TEXT_ENTRY 结束条件：

blur
submit
navigation
切换到另一个输入控件
明确的后续 semantic action

可以使用 timeout 作为 fallback，但不能因为普通 key gap 较大就轻易拆成多个 TEXT_ENTRY。

==================================================
11. CLICK / Activation Recognizer
==================================================

CLICK 代表：

“激活了一个普通可点击元素”。

典型：

mousemove
mousemove
pointerdown
mousedown
pointerup
mouseup
click

→

CLICK(target_role)

CLICK 的 start_time：

使用 activation cycle 中第一个：

pointerdown / mousedown

不要使用此前的 mousemove。

因为 mousemove 属于 pointer transportation，而不是 click activation duration。

CLICK 的 end_time：

使用 click 时间。

如果只有 click，没有 down/up：

start_time = end_time = click time

仍然输出 CLICK。

不要因为某个 framework 没有 down/up 就丢弃动作。

mousemove trajectory、curvature、transition distance 等继续留在 E / Execution View 中，不进入 Z。

==================================================
12. SELECT Recognizer
==================================================

SELECT 主要针对：

SELECT role
combobox
listbox

典型：

click(select)
input(select)
change(select)

初步可能识别：

CLICK(SELECT)
SELECT(SELECT)

最终必须规约为：

SELECT(SELECT)

不能输出：

CLICK → SELECT

因为 CLICK 只是实现 SELECT 的 mechanical primitive。

SELECT 的强证据：

change on SELECT
input/change indicating selected value changes

如果仅仅：

click(select)

没有 selection/change 证据，

则不能强行判断已经发生 SELECT。

此时保留：

CLICK(SELECT)

==================================================
13. TOGGLE Recognizer
==================================================

针对：

checkbox
radio
switch

典型：

click(toggle)
input(toggle)
change(toggle)

如果 checked / selected state 确实改变：

→

TOGGLE(TOGGLE)

最终不要同时保留 CLICK。

即：

CLICK(TOGGLE)
+
state change

→

TOGGLE(TOGGLE)

如果仅：

click(toggle)

但没有 change / checked state change 证据，

则不能断言 TOGGLE 成功。

保留：

CLICK(TOGGLE)

这样可以区分：

尝试点击

和

实际改变状态。

==================================================
14. SUBMIT Recognizer
==================================================

必须加入 SUBMIT。

原因：

不同 framework 可能通过完全不同的底层方式完成同一个提交动作。

例如：

Framework A：

click(submit_button)
submit(form)

Framework B：

keydown(Enter)
submit(form)

二者最终都应该：

SUBMIT

规则：

CLICK(BUTTON with submit semantics)
+
submit(form)

→

SUBMIT

keydown(Enter)
+
submit(form)

→

SUBMIT

不能保留：

CLICK → SUBMIT

也不能保留：

TEXT_ENTRY → SUBMIT

如果 submit 是前述机械事件所产生的同一个高层行为，则 mechanical trigger 应被 SUBMIT 吸收。

注意：

TEXT_ENTRY 本身如果真实发生过，应保留。

例如：

TEXT_ENTRY
keydown(Enter)
submit

最终应该：

TEXT_ENTRY
SUBMIT

而不是只剩 SUBMIT。

==================================================
15. SCROLL Recognizer
==================================================

wheel 不等于 scroll。

wheel 只是输入设备事件。

真正的 SCROLL 必须依据：

scroll position 实际发生变化。

优先采集：

scroll
scrollend

如果目前没有，请加入。

一次浏览器 scroll command 可能产生多个 scroll event。

例如：

scroll y=10
scroll y=23
scroll y=45
scroll y=74
scroll y=105
scrollend

必须归并为：

SCROLL(PAGE)

而不是：

SCROLL
SCROLL
SCROLL
SCROLL
SCROLL

SCROLL burst 识别规则：

开始：
    第一次实际 scroll position change。

继续：
    target 相同，
    连续产生 scroll，
    event gap 较短，
    没有新的高层 semantic action。

结束优先级：

1. scrollend
2. event gap > configurable T_scroll_gap
3. 出现新的：
   CLICK
   TEXT_ENTRY
   SELECT
   TOGGLE
   SUBMIT
   NAVIGATION

必须避免过度合并。

例如：

SCROLL ↓
等待 2 秒
SCROLL ↓
等待 3 秒
SCROLL ↓

如果中间存在明显停顿，应保留成：

SCROLL
SCROLL
SCROLL

因为这可能表示：

观察
→ 思考
→ 再滚动
→ 思考
→ 再滚动

这可能正是 LLM / perception policy。

我们的目标仅仅是消除：

“一次实际 scroll 动作产生多个底层 scroll events”

而不是消除：

“Agent 独立决定执行了多次 scroll”。

SCROLL 可以保留一个粗粒度 direction：

UP
DOWN
LEFT
RIGHT

但不要把精确 CSS pixel distance 放进 Z_content。

精确滚动距离属于 Execution / Context View。

==================================================
16. HOVER Recognizer
==================================================

HOVER 必须非常保守。

不要因为一个 pointerover 就输出 HOVER。

原因：

cursor 在移动到 CLICK target 的途中会自然产生 pointerover。

例如：

pointerover(button)
100ms
click(button)

最终应该只输出：

CLICK(BUTTON)

不能：

HOVER(BUTTON)
CLICK(BUTTON)

HOVER 判断需要：

1. 使用 relatedTarget 判断真正从目标外进入目标，
   避免同一 button 内 span → svg 的内部移动被误判。

2. 必须存在一定 dwell time，
   例如 configurable T_hover_min。

3. 如果 hover 很快转化成同 target CLICK，
   则 HOVER 被 CLICK 吸收。

4. 如果页面存在明确 hover-dependent interaction 证据，
   可以增强置信度。

第一版可以：

采集 HOVER candidate，
但默认让识别逻辑偏保守。

==================================================
17. NAVIGATE 与 HISTORY_NAV
==================================================

必须区分：

页面跳转结果

和

Agent 独立执行的导航动作。

例如：

click(LINK)
beforeunload
monitor_start(new_document)

最终：

CLICK(LINK)

不要：

CLICK(LINK)
NAVIGATE

因为 new document 是 CLICK 的 outcome，
不是第二个独立 decision。

同理：

CLICK(BUTTON)
→ new document

一般仍保留：

CLICK(BUTTON)

可以在内部保存：

outcome = NEW_DOCUMENT

但不要额外增加 NAVIGATE token。

NAVIGATE 只用于：

出现真实页面导航，
但此前没有可以解释该导航的：

CLICK(LINK)
CLICK(BUTTON)
SUBMIT
HISTORY_NAV

例如程序化直接 navigation。

HISTORY_NAV：

根据：

popstate
history instrumentation
navigation tracking

生成：

HISTORY_NAV(PAGE)

不要简单将所有 popstate 命名为 BACK。

popstate 也可能来自 forward。

除非系统维护了 history index，能够确认方向。

若能确认，可额外保存 subtype：

BACK
FORWARD

否则统一：

HISTORY_NAV

==================================================
18. Semantic Reducer
==================================================

这是整个 E → Z 的核心。

Recognizer 首先产生 candidate actions。

Candidate 之间允许重叠。

Reducer 再处理：

优先级
吸收关系
重复 candidate
semantic folding

必须至少实现以下规则：

CLICK(TEXT_INPUT)
+
TEXT_ENTRY(TEXT_INPUT)
→
TEXT_ENTRY(TEXT_INPUT)

CLICK(SELECT)
+
SELECT(SELECT)
→
SELECT(SELECT)

CLICK(TOGGLE)
+
实际 state change
→
TOGGLE(TOGGLE)

CLICK(submit button)
+
SUBMIT
→
SUBMIT

keydown(Enter)
+
SUBMIT
→
SUBMIT

HOVER(target)
+
短时间内 CLICK(same target)
→
CLICK(target)

wheel / touchmove
+
实际 scroll burst
→
SCROLL

CLICK(LINK)
+
document navigation
→
CLICK(LINK)

即：

越接近最终“用户 / Agent 意图”的动作优先级越高。

机械 primitive 越容易被吸收。

建议大致优先级：

SUBMIT
>
TEXT_ENTRY / SELECT / TOGGLE
>
HISTORY_NAV / NAVIGATE
>
CLICK
>
HOVER

SCROLL 通常独立处理。

==================================================
19. 不允许的过度语义化
==================================================

不要根据：

button text
页面文字
URL 内容
产品名
输入内容
CSS class
DOM id

推断：

SEARCH
BUY
COMPARE
LOGIN
CHECKOUT

这种 task-specific semantic action。

第一版只做页面无关的 interaction abstraction。

另外：

Z 中不能直接包含：

paste_count
keydown_count
mousemove_count
mouse curvature
mouse path
button down/up count
click x/y
scroll exact pixel distance
CSS selector
actual input text

因为这些信息会重新将 framework execution detail 泄漏进 decision representation。

==================================================
20. Timing Annotator
==================================================

最终每个 semantic action 计算：

duration_i =
    end_time_i - start_time_i

以及：

inter_action_latency_i =
    start_time_(i+1) - end_time_i

但是：

如果 action_i 和 action_(i+1) 跨 document / hard navigation boundary：

inter_action_latency_i = null

不要直接把：

page load latency
network latency
server latency
render latency

混入普通 inter-action latency。

未来可以单独定义：

page_transition_latency

放到 Temporal Orchestration View。

此外：

duration / inter_action_latency 不属于纯 Decision Content。

建议最终保存两个 view：

Z_content:
    action_type
    target_role
    optional coarse subtype/outcome

Z_temporal:
    action_type
    target_role
    duration
    inter_action_latency

这样未来可以分别训练：

LLM(Z_content)

和：

LLM(Z_temporal)

用于判断识别能力来自：

action policy

还是：

timing / orchestration side channel。

==================================================
21. Source Event Span
==================================================

每一个 SemanticAction 都必须能追溯到原始 E。

例如：

Z[3] = TEXT_ENTRY(TEXT_INPUT)

必须能够查询：

source_event_start_index = 41
source_event_end_index = 67

即：

Z[3] ↔ E[41:67]

semantic abstraction 不能删除原始事件。

最终数据同时保留：

raw event sequence E
semantic sequence Z
existing 98-D features

这样后续可以分别研究：

E → execution / framework fingerprint

Z → decision / LLM fingerprint

==================================================
22. Recognition Confidence
==================================================

内部可以为 candidate / final action 保存 confidence：

HIGH
MEDIUM
LOW

或者数值。

例如：

TEXT_ENTRY:
input/change evidence → HIGH

SELECT:
真实 select change → HIGH
仅 click(select) → 不输出 SELECT

TOGGLE:
checked value changed → HIGH

HOVER:
pointerover + dwell，没有明确页面变化 → MEDIUM/LOW

confidence 只用于：

debug
parser evaluation
过滤实验

默认不能直接作为模型输入。

==================================================
23. Configurable Thresholds
==================================================

所有时间阈值集中定义，不要散落 magic numbers。

例如：

T_SCROLL_GAP_MS
T_HOVER_MIN_MS
T_ACTIVATION_MAX_GAP_MS
T_TEXT_ENTRY_IDLE_MS
T_NAV_CAUSAL_WINDOW_MS

这些参数统一放到：

SemanticActionConfig

不要硬编码在 recognizer 内。

第一版可以给合理默认值，但代码结构必须方便后续实验调参。

==================================================
24. 推荐输出格式
==================================================

建议输出 JSON：

{
  "schema": "semantic-actions/v1",

  "actions": [
    {
      "index": 0,
      "action_type": "TEXT_ENTRY",
      "target_role": "TEXT_INPUT",

      "start_time_ms": ...,
      "end_time_ms": ...,

      "duration_ms": ...,
      "inter_action_latency_ms": ...,

      "source_event_start_index": ...,
      "source_event_end_index": ...,

      "document_id": ...,

      "recognition_rule": "...",
      "confidence": "HIGH"
    }
  ]
}

另外输出适合直接进入模型的序列：

action_sequence_content = [
    ["TEXT_ENTRY", "TEXT_INPUT"],
    ["SUBMIT", "OTHER"],
    ["SCROLL", "PAGE"],
    ["CLICK", "LINK"]
]

以及：

action_sequence_temporal = [
    ["TEXT_ENTRY", "TEXT_INPUT", duration, gap],
    ["SUBMIT", "OTHER", duration, null],
    ...
]

==================================================
25. 一个完整示例
==================================================

输入 E：

monitor_start

pointerover(input)
pointerdown(input)
mousedown(input)
pointerup(input)
mouseup(input)
click(input)
focus(input)

keydown(h)
input(h)
keyup(h)

keydown(i)
input(hi)
keyup(i)

change(input)

pointerover(button)
pointerdown(button)
mousedown(button)
pointerup(button)
mouseup(button)
click(button)

submit(form)

beforeunload
monitor_start(page2)

wheel
scroll
scroll
scroll
scrollend

pointerover(link)
pointerdown(link)
mousedown(link)
pointerup(link)
mouseup(link)
click(link)

beforeunload
monitor_start(page3)

第一阶段 candidate：

CLICK(TEXT_INPUT)
TEXT_ENTRY(TEXT_INPUT)

CLICK(BUTTON)
SUBMIT

SCROLL(PAGE)

CLICK(LINK)

经过 SemanticReducer：

Z = [
    TEXT_ENTRY(TEXT_INPUT),
    SUBMIT,
    SCROLL(PAGE),
    CLICK(LINK)
]

不能输出：

CLICK(TEXT_INPUT)
TEXT_ENTRY(TEXT_INPUT)
CLICK(BUTTON)
SUBMIT
NAVIGATE
SCROLL
CLICK(LINK)
NAVIGATE

因为这会重复计算执行 primitive 和 navigation outcome。

==================================================
26. Unit Tests
==================================================

请为 Semantic Action Abstraction 编写完整 unit tests。

至少覆盖：

1.
focus + keydown/input/keyup + change
→ TEXT_ENTRY

2.
focus + paste + input + change
→ TEXT_ENTRY

3.
programmatic input + change
→ TEXT_ENTRY

4.
click input + text entry
→ 只保留 TEXT_ENTRY

5.
pointerdown + mousedown + pointerup + mouseup + click button
→ 单个 CLICK(BUTTON)

6.
只有 click，无 down/up
→ 仍然 CLICK

7.
click select + change select
→ SELECT，不保留 CLICK

8.
click checkbox + checked change
→ TOGGLE，不保留 CLICK

9.
click checkbox 无 state change
→ CLICK(TOGGLE)

10.
click submit button + submit
→ SUBMIT

11.
keydown Enter + submit
→ SUBMIT

12.
TEXT_ENTRY + Enter + submit
→ TEXT_ENTRY, SUBMIT

13.
多个连续 scroll event + scrollend
→ 一个 SCROLL

14.
三个被明显长停顿分开的 scroll burst
→ 三个 SCROLL

15.
wheel 但页面位置不变
→ 不产生 SCROLL

16.
pointerover + 很快 click same target
→ 不产生 HOVER，只 CLICK

17.
长期 hover 且无 click
→ HOVER

18.
click link + new document
→ CLICK(LINK)，不额外 NAVIGATE

19.
无前置 click/submit 的直接 URL navigation
→ NAVIGATE

20.
popstate
→ HISTORY_NAV

21.
action 不跨 document

22.
clock rollback 时 flush candidate

23.
nested span/svg inside button
→ target_role BUTTON

24.
click option / nested select target
→ target_role SELECT

25.
pointer/mouse duplicated down/up
→ 不产生重复 action

==================================================
27. Parser Debug 工具
==================================================

请额外提供一个 debug 输出函数。

给定一个 session，可以输出：

Raw Event:
E[41] pointerdown BUTTON
E[42] mousedown BUTTON
E[43] pointerup BUTTON
E[44] mouseup BUTTON
E[45] click BUTTON

↓

Candidate:
CLICK(BUTTON)

↓

Final:
Z[7] CLICK(BUTTON)

另一个：

E[10] click TEXT_INPUT
E[11] focus TEXT_INPUT
E[12..30] key/input events
E[31] change TEXT_INPUT

↓

Candidates:
CLICK(TEXT_INPUT)
TEXT_ENTRY(TEXT_INPUT)

↓

Reduction:
CLICK absorbed by TEXT_ENTRY

↓

Final:
TEXT_ENTRY(TEXT_INPUT)

这个 debug 功能非常重要，后续需要人工抽查 parser 是否真的在抽象语义，而不是错误吞掉行为。

==================================================
28. 实现理念总结
==================================================

请严格遵守以下原则：

第一：
E → Z 不是单事件分类问题，而是“事件区间解析问题”。

第二：
多个 raw events 可以形成一个 semantic action。

第三：
多个 candidate semantic actions 还可能进一步被更高层 action 吸收。

第四：
同一高层意图如果由不同 framework 产生不同 raw event pattern，应尽可能映射到相同 Z。

第五：
不要把 task-specific text / DOM identifier / exact coordinates 等 shortcut 放入 Z。

第六：
不要把 framework-specific execution details，例如 paste、keypress 数量、mouse trajectory，重新放入 Z。

第七：
所有 final semantic action 必须可追溯到原始 event span。

第八：
Semantic abstraction 必须 deterministic、可复现、可测试。

第九：
对模糊情况宁可保守地保留较低层动作，也不要凭空推断高层语义。

例如：

click(toggle) 但无状态改变：
保留 CLICK(TOGGLE)，
而不是猜测 TOGGLE。

第十：
最终必须同时保留：

E：原始事件序列
Z：语义动作序列
现有 v5 98-D statistical features

三者是三个互补的数据视图，而不是互相替代。

==================================================
29. 最终需要交付
==================================================

请实现：

1. Semantic Action schema。
2. Canonical target-role normalization。
3. Event normalizer。
4. 各 action recognizer。
5. Candidate interval representation。
6. Semantic reducer。
7. Timing annotator。
8. E → Z 主入口函数。
9. JSON serialization。
10. Debug trace 输出。
11. Unit tests。
12. 对主要规则添加清晰注释。

在开始编码前，请先检查当前项目的数据结构和现有事件字段，尽可能复用现有：
session segmentation
pointer/mouse dedup
navigation tracking
target metadata
timestamp normalization

不要复制一套与现有逻辑冲突的新实现。

如果现有采集缺少实现 Z 所必需的字段，请明确列出缺失项并以最小改动扩展采集器。

优先保证 semantic abstraction 的正确性、可解释性和可复现性，不要优先追求复杂架构。