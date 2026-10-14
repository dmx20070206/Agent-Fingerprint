> 历史资料：内容与结果以记录时版本为准。当前操作见[工作流](../workflow.md)，其他历史材料见[归档索引](README.md)。

# WebVoyager / Skyvern 运行修复（2026-10-12）

修复针对 `webvoyager_qwen3_5_9b_1012_1035` 和 `skyvern_qwen3_5_9b_1012_1003`。

## 生效范围

- WebVoyager：未显式指定浏览器/驱动时，从 Selenium 缓存选择同平台、同完整版本的一对可执行文件，并把两个路径传给 WebVoyager 子进程。存在任意 `WEBVOYAGER_CHROME_BINARY`、`WEBVOYAGER_CHROMEDRIVER`、`CHROME_BINARY`、`CHROMEDRIVER` 配置时保留用户选择。没有匹配缓存时沿用 Selenium 原有发现机制。
- Skyvern：仅本地 embedded 模式且模型别名为 `qwen3.5-*`（可带 provider 前缀）时启用下面的恢复逻辑。远程 Skyvern 和其他模型沿用原逻辑。
- 不修改共享浏览器缓存、第三方源码、公共模型网关或 vLLM 配置；不重启服务。新进程生效，已运行的 agent 不会被注入补丁。

## Skyvern 恢复逻辑

1. 本机原 Chromium 128 在最小空白 input 页面上，`fill()` / `keyboard.insert_text()` 均返回但字段仍为空。相同 Playwright 环境使用缓存 Chrome 153 时正常。Qwen 子进程优先使用匹配缓存中的 Chrome；可用 `SKYVERN_CHROME_EXECUTABLE_PATH` 显式覆盖（其次尊重已有 `CHROME_EXECUTABLE_PATH`）。通过 Skyvern 的 headless launch 参数传入，不使用共享 CDP 会话。
2. 仅当访问 loopback 地址且存在 flights 表单和日历控件时，恢复 `from`、`to`、`flight-date` 输入。城市按普通文本填写并回读；日期为 today 或浏览器当地当天日期时，通过点击日期框和 Today 设置，再回读。其他日期打开日历后返回失败反馈，要求选择具体日期，绝不擅自改成 today。回读失败会停止当前动作批次。
3. 动作提示要求依据当前字段和失败反馈推进；先搜索、选择实际航班，再点击启用的 Next，无进展时改变交互策略。航班选择仍由 agent 决定，没有硬编码 UA800 自动完成任务。
4. `parse-input-or-select-context` 使用合法 JSON 示例，不要求 thought，明确区分城市文本框和地址自动补全。该辅助调用单独限制为 512 tokens，可用 `SKYVERN_QWEN_CONTEXT_MAX_TOKENS` 修改；不会提高用户已有的更低预算。其他调用保留原有生成参数和预算。

这是带有本地 flights 控件适配的修复版本；与旧运行比较行为指纹时，应记录代码版本和实际浏览器版本。

## 验证

- 54 项适配器/配置回归测试通过；基础 Python 缺少 Selenium 时，4 项现有交互测试跳过。
- WebVoyager 环境实际启动 Chrome 153.0.8010.52 / ChromeDriver 153.0.8010.52 并完成页面导航。
- 已安装 Skyvern SDK 的提示词/handler 补丁加载正常；实际使用补丁后的 launch 参数创建 persistent context，输入回读正常。
- 真实 Playwright 回归测试：复现日期不能直接输入；恢复城市和 Today；Search → UA800 → Next 进入详情页；其他日期不会误选 Today；其他字段和非 flights 页面交回上游处理。
- 本地 Qwen 3.5 9B 辅助提示单次对照：原提示 176 tokens，修改后 50 tokens，均为合法 JSON、finish_reason=stop。该样本没有复现旧日志的 8192-token 截断，不能据此确定其唯一原因。

未重跑完整的模型驱动订票流程，以上验证不等同于端到端任务成功率保证。

```bash
python -m pytest -q tests/test_agent_failure_regressions.py tests/test_skyvern_generation.py tests/test_adapters.py
# 在具有 Playwright 和浏览器的 Skyvern 环境中：
PYTHONPATH="$PWD/src" python -m unittest discover -s tests -p test_skyvern_browser_recovery.py -v
```

## WebVoyager + Qwen 后续修复（1107 运行）

新增项目内 `runners/webvoyager.py`，仅 `qwen3.5-*` 经 WebVoyagerAdapter 启动时加载。其他模型继续直接执行原框架入口；不修改 third_party checkout 或共享 OpenAI SDK。

- 请求显式设置 `enable_thinking=false`，默认正文预算 2048 tokens，可用 `WEBVOYAGER_QWEN_MAX_TOKENS` 调整。保留原重试机制，新增 finish_reason、completion_tokens、正文/推理是否存在的诊断，不记录推理正文。
- 视觉观察附加浏览器本地日期、禁用控件以及每个标签所在滚动区域的位置。提示 Today 在视口外时先滚动，不把标签数字当成日历日期。
- 当 WINDOW 滚动无位移且只有一个可见内部区域能继续滚动时，用真实 wheel 输入滚动该区域；多个候选时返回反馈，要求指定标签，避免任意选择。
- 当初始任务明确 `Date: today` 且访问本地 flights 日历时，阻止点击非当天日期，并返回恢复提示。其他日期任务不受此保护规则影响。

针对性真实 Selenium 回归覆盖：错误日期拒绝、Today 选择、内部滚动到 UA800、进入航班详情页。基础环境的相关回归为 61 passed / 1 skipped（缺 Selenium 的真实浏览器测试）；WebVoyager 环境单独运行这项浏览器测试及请求测试，3 项通过。
