# 浏览器行为指纹统计特征

`analysis/l3_browser_dynamic.py` 输出 60 维特征。无法从原始事件可靠计算的数值为 `-1.0`；计数类特征始终返回非负整数。注入 probe 将 `pointermove`、`pointerdown`、`pointerup`（以及取消）规范化为 `mousemove`、`mousedown`、`mouseup`，不会重复统计；probe 不安装 `MutationObserver`，也不记录 mutation 事件。

| Rank | 特征 | 类型 | 计算方法 |
|---:|---|---|---|
| 1 | `paste_count` | Count | `paste` 事件数量 |
| 2 | `mouse_curvature_angle_range` | Numeric | 连续鼠标移动曲率角度范围 |
| 3–5 | `hold_latency_median/mean` | Numeric | 完整 keydown→keyup 时长统计 |
| 4,15,23,31 | `inter_key_latency_*` | Numeric | 连续完整击键 keydown 时间差统计 |
| 6,8,12,17 | `scroll_distance_*` | Numeric | 相邻滚动位置变化的 std/mean/range/median |
| 7 | `change_count` | Count | `change` 数量 |
| 9,16,20,28 | `scroll_time_*` | Numeric | 相邻滚动事件时间差统计 |
| 10 | `input_count` | Count | `input` 数量 |
| 11 | `mouse_event_count` | Count | mousemove/mousedown/mouseup/click 总数 |
| 13,39,41,43,45 | `button0_count`…`button4_count` | Count | 各按钮 down+up 数量 |
| 14,29 | `hold_latency_range/std` | Numeric | 按键保持时长统计 |
| 18,25,48 | `mouse_curvature_angle_*` | Numeric | 曲率角度 mean/std/median |
| 19,37 | `dangling_keydown/keyup` | Flag | 缺失配对的键盘事件 |
| 21 | `scroll_count` | Count | `scroll` 数量 |
| 22,27,36,47 | `mouse_direction_*` | Numeric | 移动方向角统计（角度展开） |
| 24,40,42,44,46 | `button*_down_up_ratio` | Numeric | 各按钮 down / up |
| 26,33,49,50 | `mouse_curvature_distance_*` | Numeric | 曲率距离统计 |
| 30 | `scrollend_count` | Count | `scrollend` 数量 |
| 32 | `backspace_delete_count` | Count | 完整 Backspace/Delete 击键数 |
| 34 | `mousemove_count` | Count | 规范化后的 `mousemove` 数量 |
| 35 | `keypress_count` | Count | `keydown` 数量 |
| 38 | `backspace_delete_ratio` | Numeric | Backspace/Delete 占完整击键比例 |
| 51 | `scroll_reversals` | Count | 滚动深度序列方向反转次数 |
| 52 | `structural_key_ratio` | Numeric | Tab/方向键/Esc/Backspace 与可打印字符比例 |
| 53–54 | `mean_key_iei_ms`, `std_key_iei_ms` | Numeric | 连续逻辑输入思考间隔平均值及标准差 |
| 55–56 | `click_x_std`, `click_y_std` | Numeric | 点击坐标 X/Y 标准差 |
| 57 | `click_bbox_area_frac` | Numeric | 点击边界框面积 / viewport 面积 |
| 58 | `link_click_ratio` | Numeric | 链接点击数 / 点击总数 |
| 59 | `nav_to_click_ratio` | Numeric | 导航事件 / 点击数 |
| 60 | `mean_exit_scroll_pct` | Numeric | `beforeunload` 前滚动深度百分比平均值 |

行为分组对象使用上述同名字段；旧的 presence 字段已改为 count 字段。
