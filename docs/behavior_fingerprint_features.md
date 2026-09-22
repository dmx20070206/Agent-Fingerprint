# 浏览器行为指纹统计特征（50 维）

下表汇总浏览器行为指纹的 50 个统计维度。序号是固定的特征位置（1–50），不表示训练分类器后的重要性或优先级。Boolean 编码为 `True=1 / False=0`；Numeric 使用浮点数。特定任务中因没有足够原始事件、统计样本不足或比例分母为 0 而无法计算时，统一编码为 **-1**。`std` 使用总体标准差（`ddof=0`），`range=max−min`，时间单位为 ms，距离单位为 CSS 像素。

| 维度（Rank） | 特征 | 数值类型 | 计算方法（由浏览器原始事件聚合） |
|---:|---|---|---|
| 1 | 粘贴事件存在性 | Boolean | 出现至少一个 `paste` 事件则 1，否则 0 |
| 2 | 鼠标移动曲率角度范围 | Numeric | 连续 `mousemove` 段中曲率角度的 `max−min` |
| 3 | 按键保持延迟中位数 | Numeric | 每个完整 `keydown→keyup` 的 `keyup.ts−keydown.ts`，取 median |
| 4 | 按键间隔延迟中位数 | Numeric | 相邻完整击键的 `keydown` 时间差，取 median |
| 5 | 按键保持延迟平均值 | Numeric | hold latency 的 mean |
| 6 | 滚动距离标准差 | Numeric | 各滚动段（或相邻滚动位置变化）的距离 std |
| 7 | `change` 事件数量 | Numeric | `count(change)` |
| 8 | 滚动距离平均值 | Numeric | 滚动距离 mean |
| 9 | 滚动时间中位数 | Numeric | 每个滚动段持续时间（末事件 ts−首事件 ts）的 median |
| 10 | `input` 事件数量 | Numeric | `count(input)` |
| 11 | 鼠标事件总数量 | Numeric | 固定事件集合（`mousemove`、`mousedown`、`mouseup` 等）总 count |
| 12 | 滚动距离范围 | Numeric | 滚动距离的 `max−min` |
| 13 | 鼠标按钮 0 事件存在性 | Boolean | 出现 button=0 的 `mousedown` 或 `mouseup` 则 1 |
| 14 | 按键保持延迟范围 | Numeric | hold latency 的 `max−min` |
| 15 | 按键间隔延迟平均值 | Numeric | 相邻完整击键 keydown 间隔的 mean |
| 16 | 滚动时间平均值 | Numeric | 滚动段持续时间 mean |
| 17 | 滚动距离中位数 | Numeric | 滚动距离 median |
| 18 | 鼠标移动曲率角度平均值 | Numeric | 曲率角度 mean |
| 19 | 悬空 keydown 存在性 | Boolean | 存在无对应 `keyup` 的 `keydown` 则 1 |
| 20 | 滚动时间标准差 | Numeric | 滚动段持续时间 std |
| 21 | 滚动事件存在性 | Boolean | 出现至少一个 `scroll` 事件则 1 |
| 22 | 鼠标移动方向平均值 | Numeric | 每段移动方向角（由相对 dx、dy 计算）的 mean |
| 23 | 按键间隔延迟范围 | Numeric | keydown 间隔的 `max−min` |
| 24 | 鼠标按钮 0 按下/抬起比率 | Numeric | `count(button0 mousedown) / count(button0 mouseup)` |
| 25 | 鼠标移动曲率角度标准差 | Numeric | 曲率角度 std |
| 26 | 鼠标移动曲率距离中位数 | Numeric | 曲率距离 median |
| 27 | 鼠标移动方向范围 | Numeric | 移动方向的 `max−min`（角度展开规则须固定） |
| 28 | 滚动时间范围 | Numeric | 滚动段持续时间的 `max−min` |
| 29 | 按键保持延迟标准差 | Numeric | hold latency std |
| 30 | 滚动结束事件存在性 | Boolean | 出现至少一个 `scrollend` 事件则 1 |
| 31 | 按键间隔延迟标准差 | Numeric | keydown 间隔 std |
| 32 | 退格/删除键敲击总数 | Numeric | 完整击键中 `Backspace` 或 `Delete` 的 count |
| 33 | 鼠标移动曲率距离平均值 | Numeric | 曲率距离 mean |
| 34 | `mousemove` 事件存在性 | Boolean | 出现至少一个 `mousemove` 则 1 |
| 35 | 完整击键动作存在性 | Boolean | 至少一个完整 `keydown→keyup` 则 1 |
| 36 | 鼠标移动方向标准差 | Numeric | 移动方向 std |
| 37 | 悬空 keyup 存在性 | Boolean | 存在无对应 `keydown` 的 `keyup` 则 1 |
| 38 | 退格/删除键占总击键次数比例 | Numeric | `count(Backspace/Delete) / count(完整击键)` |
| 39 | 鼠标按钮 1 事件存在性 | Boolean | 出现 button=1 的 `mousedown` 或 `mouseup` 则 1 |
| 40 | 鼠标按钮 1 按下/抬起比率 | Numeric | `count(button1 mousedown) / count(button1 mouseup)` |
| 41 | 鼠标按钮 2 事件存在性 | Boolean | 出现 button=2 的 `mousedown` 或 `mouseup` 则 1 |
| 42 | 鼠标按钮 2 按下/抬起比率 | Numeric | `count(button2 mousedown) / count(button2 mouseup)` |
| 43 | 鼠标按钮 3 事件存在性 | Boolean | 出现 button=3 的 `mousedown` 或 `mouseup` 则 1 |
| 44 | 鼠标按钮 3 按下/抬起比率 | Numeric | `count(button3 mousedown) / count(button3 mouseup)` |
| 45 | 鼠标按钮 4 事件存在性 | Boolean | 出现 button=4 的 `mousedown` 或 `mouseup` 则 1 |
| 46 | 鼠标按钮 4 按下/抬起比率 | Numeric | `count(button4 mousedown) / count(button4 mouseup)` |
| 47 | 鼠标移动方向中位数 | Numeric | 移动方向 median |
| 48 | 鼠标移动曲率角度中位数 | Numeric | 曲率角度 median |
| 49 | 鼠标移动曲率距离范围 | Numeric | 曲率距离 `max−min` |
| 50 | 鼠标移动曲率距离标准差 | Numeric | 曲率距离 std |

**鼠标移动段定义：**连续 `mousemove` 事件序列在发生点击（`mousedown+mouseup`）或相邻事件空闲超过 250 ms 时结束；距离、方向和曲率均使用相对坐标计算，与屏幕分辨率无关。
