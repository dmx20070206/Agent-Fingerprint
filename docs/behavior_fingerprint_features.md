# 浏览器行为指纹统计特征（v5）

[文档导航](README.md) · [数据说明](data-contract.md)

统计特征把一次完整运行概括成 98 个数值，例如点击数量、击键间隔和滚动距离。本文用于查询各列含义；日常提取只需下面的命令。

`src/agent_fingerprint/features/feature_schema.py` 是特征名称和顺序的唯一代码定义；共 **98 维**。
`src/agent_fingerprint/features/l3_browser_dynamic.py` 输出 `agent-fingerprint-browser-features/v5`，
同时提供 features、feature_names、feature_vector 和五个行为分组；这些分组是同一组特征的视图。

## 提取示例

```bash
python -m agent_fingerprint extract \
  --input-file examples/semantic_actions/example_raw.json \
  --output-file data/results/features_demo.json
```

`--input-file` 指定原始事件，`--output-file` 指定结果。输出的 `existing_statistics` 包含 v5 统计，`feature_names` 与 `feature_vector` 一一对应；缺失值为 `null`，不等于 0。
批量训练数据请使用 [dataset](data-contract.md)，无需逐个提取。语义动作输出说明见[语义指南](semantic-actions.md)。

| 常用参数 | 作用 |
| --- | --- |
| `--input-file` | 一份原始 L3 JSON 或事件数组 |
| `--output-file` | 输出 JSON；本页示例保存在 data/results/ 下 |
| `--debug-output` | 可选，输出语义动作的识别过程；不是统计计算日志 |

## 统计语义

键盘 hold_latency 是完整 keydown→keyup 的时长；inter_key_latency 是同段相邻完整击键的 keydown 时间差，不等同于 LLM 思考时间。
button_count 是 down+up 数量，不是完整点击次数。keypress_count 统计 keydown。
mouse_curvature_distance 是移动段内相邻点的欧氏距离；mouse_curvature_angle 是方向变化角，不是微分几何曲率。
scroll_distance 是同一滚动目标相邻滚动位置的欧氏距离。std 使用总体标准差；std/range 少于两个观测时为 null，mean/median 无观测时为 null；比率分母为零时为 null。

连续鼠标轨迹在点击/按下/释放、超过 250ms 间隔、无效坐标/时间处分段；距离、转向角、方向角展开均在段内计算。
会话变化、时钟回退、导航/页面离开会断开事件段；键盘配对与间隔、滚动距离与反转不跨段，不同滚动目标单独统计。
方向角四种统计统一使用段内展开后的角度；转向角为相邻方向的最短有符号角差，合法负角度保留。
pointer/mouse 去重仅删除相邻配对事件，不删除真正相邻的 mousemove。
缺失值使用 JSON null（Python None），计数零仍为 0；viewport 不可用时点击面积比例为 null。

## 固定列顺序

| 位置 | 特征 |
|---:|---|
| 1 | `paste_count` |
| 2 | `mouse_curvature_angle_range` |
| 3 | `hold_latency_median` |
| 4 | `inter_key_latency_median` |
| 5 | `hold_latency_mean` |
| 6 | `scroll_distance_std` |
| 7 | `change_count` |
| 8 | `scroll_distance_mean` |
| 9 | `scroll_time_median` |
| 10 | `input_count` |
| 11 | `mouse_event_count` |
| 12 | `scroll_distance_range` |
| 13 | `button0_count` |
| 14 | `hold_latency_range` |
| 15 | `inter_key_latency_mean` |
| 16 | `scroll_time_mean` |
| 17 | `scroll_distance_median` |
| 18 | `mouse_curvature_angle_mean` |
| 19 | `dangling_keydown` |
| 20 | `scroll_time_std` |
| 21 | `scroll_count` |
| 22 | `mouse_direction_mean` |
| 23 | `inter_key_latency_range` |
| 24 | `button0_down_up_ratio` |
| 25 | `mouse_curvature_angle_std` |
| 26 | `mouse_curvature_distance_median` |
| 27 | `mouse_direction_range` |
| 28 | `scroll_time_range` |
| 29 | `hold_latency_std` |
| 30 | `scrollend_count` |
| 31 | `inter_key_latency_std` |
| 32 | `backspace_delete_count` |
| 33 | `mouse_curvature_distance_mean` |
| 34 | `mousemove_count` |
| 35 | `keypress_count` |
| 36 | `mouse_direction_std` |
| 37 | `dangling_keyup` |
| 38 | `backspace_delete_ratio` |
| 39 | `button1_count` |
| 40 | `button1_down_up_ratio` |
| 41 | `button2_count` |
| 42 | `button2_down_up_ratio` |
| 43 | `button3_count` |
| 44 | `button3_down_up_ratio` |
| 45 | `button4_count` |
| 46 | `button4_down_up_ratio` |
| 47 | `mouse_direction_median` |
| 48 | `mouse_curvature_angle_median` |
| 49 | `mouse_curvature_distance_range` |
| 50 | `mouse_curvature_distance_std` |
| 51 | `scroll_reversals` |
| 52 | `structural_key_ratio` |
| 53 | `click_x_std` |
| 54 | `click_y_std` |
| 55 | `click_bbox_area_frac` |
| 56 | `link_click_ratio` |
| 57 | `nav_to_click_ratio` |
| 58 | `mean_exit_scroll_pct` |
| 59 | `mouse_transition_distance_mean` |
| 60 | `mouse_transition_distance_std` |
| 61 | `mouse_transition_direction_mean` |
| 62 | `mouse_transition_direction_std` |
| 63 | `mouse_transition_turn_angle_mean` |
| 64 | `mouse_transition_turn_angle_std` |
| 65 | `mouse_transition_interval_mean` |
| 66 | `mouse_transition_interval_std` |
| 67 | `mouse_isolated_move_ratio` |
| 68 | `n_clicks` |
| 69 | `n_navigations` |
| 70 | `n_focus` |
| 71 | `n_events_total` |
| 72 | `page_count` |
| 73 | `n_unique_domains` |
| 74 | `total_duration_s` |
| 75 | `t_first_action_ms` |
| 76 | `mean_iei_ms` |
| 77 | `std_iei_ms` |
| 78 | `median_iei_ms` |
| 79 | `p10_iei_ms` |
| 80 | `p90_iei_ms` |
| 81 | `iei_trend` |
| 82 | `mean_click_iei_ms` |
| 83 | `std_click_iei_ms` |
| 84 | `mean_nav_iei_ms` |
| 85 | `std_nav_iei_ms` |
| 86 | `max_page_dwell_ms` |
| 87 | `mean_key_iei_ms` |
| 88 | `std_key_iei_ms` |
| 89 | `max_scroll_pct` |
| 90 | `mean_scroll_pct` |
| 91 | `n_deep_scrolls` |
| 92 | `click_top_frac` |
| 93 | `n_link_clicks` |
| 94 | `popstate_ratio` |
| 95 | `scroll_to_click_ratio` |
| 96 | `actions_per_page` |
| 97 | `keydowns_per_page` |
| 98 | `focus_per_page` |

## 离散操作位置序列

按原始顺序提取有效有限 `client_x/y` 的移动事件，复用相邻 pointer/mouse 配对去重。
只在会话变化、导航、页面卸载或时间回退处分段；点击、滚动和长时间等待不会断开。
这描述视口中的操作位置变化，跨滚动也不是页面目标之间的距离，更不是真实移动曲率。
无移动事件时保持缺失，不用点击坐标补充移动轨迹。

| 特征 | 含义 |
|---|---|
| `mouse_transition_distance_mean/std` | 相邻有效位置的欧氏距离，单位 CSS px；同坐标贡献零距离 |
| `mouse_transition_direction_mean/std` | 非零位移的方向角，弧度；各会话段内先展开角度，再合并统计，与连续方向特征约定一致 |
| `mouse_transition_turn_angle_mean/std` | 相邻两次非零跳转的有符号转向角，弧度，范围 [-π, π]；零位移不产生方向且中断转向角配对 |
| `mouse_transition_interval_mean/std` | 相邻有效位置事件的时间间隔，毫秒；两端时间有效才纳入，包含 Agent 思考及操作等待，不能当作移动耗时或用来推导速度 |
| `mouse_isolated_move_ratio` | 去重后的有效坐标移动点中，未参与连续轨迹非零位移的点数占比；连续轨迹仍要求时间有效、间隔 ≤250ms 且不跨点击 |

缺失坐标的事件不纳入离散序列；缺失时间的有效位置仍提供几何观测。
标准差使用总体标准差，少于两个观测时为 null；无有效移动点时孤立占比为 null。

## 回合统计

`episode_behavior` 分组包含第 68–98 列。
滚动次数和按键次数分别使用 `scroll_count`、`keypress_count`，不单独定义 `n_scrolls`、`n_keydowns`。

计算口径：

- 总事件数及全局 IEI 使用原始事件列表，包含 monitor_start/stop 等生命周期事件，不对 pointer/mouse 配对去重。按类型计数使用相同原始列表。
- 独立页面按完整非空 URL 去重（含 query/hash）；域名按 URL hostname 去重（不计端口）。非空轨迹完全缺少 URL 时，这两项及依赖页面数的比率为 null。
- n_focus 只计 target.tag 为 input/textarea 的 focus。
- 回合起止优先读取 raw.started_at/finished_at，否则读取 manifest 同名字段，要求带时区的 ISO 时间。CLI、dataset、predict 均接入 manifest。没有边界时总时长为 null，不以首尾事件跨度冒充回合总时长。
- t_first_action_ms 按“第一次事件”定义，使用最早 epoch_ms 减回合开始时间，包含 monitor_start；缺失或负值为 null。
- IEI 优先使用 epoch_ms；缺失时只在同 session 内使用 monotonic_ms，负间隔丢弃，不拼接跨文档本地时钟。时间单位为毫秒，std 为总体标准差，少于两条间隔时为 null。百分位采用线性插值。
- iei_trend 按有效间隔序列的前后两半计算均值比；奇数条时后半多一条，前半均值为零时为 null。
- mean_key_iei_ms/std_key_iei_ms 使用所有 keydown，不要求 keyup 配对，因此与 inter_key_latency 不同。
- n_navigations 计后续文档 monitor_start（初次不计）及 navigate/navigation/popstate。相同 session 的重复 monitor_start 不重复计数；beforeunload/pagehide 不作为导航计数。导航均值/标准差使用这些导航间隔。
- max_page_dwell_ms 使用文档进入或显式导航，到下一次导航、离开、monitor_stop 或已知回合结束的时长；没有结束证据的访问不参与。它是访问时长，不是页面主动阅读时长。
- 滚动百分比使用 scroll_pct，或 100*y/(document_height-viewport_height)，限制在 0–100。页面不可滚动或缺少数据时为 null；深滚动严格大于 60%。部分有效时只统计有效观测。
- click_top_frac 在有有效 Y 坐标的点击中计算 y<192 的比例。n_link_clicks 要求 target.tag=a 且 href 非 null；anchor 缺少 href 字段时为 null；探针采集 href。
- popstate_ratio 计 reason=popstate 或 type=popstate 占导航次数的比例。探针记录 history.pushState/replaceState 的 URL 变化及 popstate，并在 monitor_start 标记 navigation_tracking；轨迹没有采集能力标记、也无 popstate 证据时为 null。popstate 可由后退或前进触发，不仅限于后退。
- 所有比率分母为零或缺失时为 null；没有事件的计数为 0。

## 比率口径与数据要求

- `click_bbox_area_frac` 使用实际视口面积。
- `link_click_ratio` 按 anchor tag 计数，不要求 href 非 null。
- `nav_to_click_ratio` 的导航计数包含 beforeunload、pagehide、navigate、navigation 事件。
- `structural_key_ratio` 的结构键集合为 Tab、ArrowUp、ArrowDown、ArrowLeft、ArrowRight、Escape、Esc、Backspace；分母为结构键次数与单字符可打印键次数之和。

上述比率按各自定义计算，不一定等于回合统计中的计数直接相除。`scroll_reversals` 按事件段、滚动目标分别计算。

数据集和模型应使用一致的 98 维特征 schema。原始数据中未采集的 history 操作、href 或回合边界无法恢复，缺失值按上述规则保留。
