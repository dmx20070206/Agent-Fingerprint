# 项目整理验收（2026-10-08）

[文档导航](../README.md) · **历史验证记录**：测试数量与状态仅对应本文所述重构版本。当前操作见[安装](../setup.md)和[架构](../architecture.md)。

实现统一到 `src/agent_fingerprint/`，README 缩为 65 行。采集运行、资源工厂、归档、统计特征、语义抽象和分析职责已拆分；实验组合集中在 YAML，第三方版本由 lock 和补丁记录。

中断后补完了以下问题：

- 实验配置中的 `${NAME:-default}` 超时值由 Python 显式解析，全部 Agent/model/task 组合可通过参数检查。
- 单次采集和任务矩阵均传递 task ID、site 标签。
- 运行扫描在 manifest 边界停止，不将框架内部 manifest 或 v1 备份识别成额外运行；身份修复保留备份原文。
- 增加稳定身份、移动幂等性、重复身份非破坏性检查和实验参数回归测试。

验证结果：

- `.venv/bin/python -m pytest -q -rs`：**236 passed, 8 skipped, 20 subtests passed**。
- 跳过项：3 项 Playwright 浏览器测试、1 项需显式启用的 LiteLLM smoke、4 项需 WebVoyager 环境的测试。
- Ruff 未定义名称检查通过；`git diff --check` 通过。
- 示例转换：22 个原始事件 → 4 个语义动作，输出保留 98 维 v5 特征；冻结 v5 回归包含在完整测试中。
- WebVoyager、Agent-E、Skyvern 的补丁校验和一致，并通过在锁定 commit 的临时 Git index 上执行 `git apply --cached --check` 验证。
- README 本地文档链接有效。

本次验收未调用付费模型，也未对已有 `data/` 执行迁移。真实 Agent 在线运行仍需对应框架环境、浏览器和模型配置。
