# 文档导航

[项目首页](../README.md)提供无需密钥的完整运行示例。首次使用先跑示例，再按下表查阅；不用从头读完所有文档。

**主流程：采集 → 数据集 → 训练 → 评估。** 已有原始运行可从数据集开始；仅查看动作则直接用 `extract`。忘记命令或混淆参数时，查[命令速查](commands.md)。

## 使用指南

| 顺序 | 文档 | 回答的问题 |
| --- | --- | --- |
| 1 | [安装](setup.md) | 需要哪些环境和依赖？密钥放哪里？ |
| 2 | [采集](collection.md) | 如何运行一个 Agent、人工采集或批量实验？ |
| 3 | [数据](data-contract.md) | 文件存在哪里？如何构建数据集、整理运行？ |
| 4 | [训练与评估](attribution-training.md) | 如何划分数据、训练四种表示、查看指标？ |
| 按需 | [语义动作](semantic-actions.md) | 如何把点击、按键等事件转成高层动作？ |
| 按需 | [逻辑回归基线](classification.md) | 如何使用原有 train/test/predict 流程？ |

## 技术参考

| 文档 | 内容 |
| --- | --- |
| [架构](architecture.md) | 代码模块、配置文件与执行流程 |
| [98 维特征](behavior_fingerprint_features.md) | 固定列顺序、计算口径、缺失值规则 |
| [训练实现](reference/attribution-details.md) | 样本校验、随机划分、时间归一化、模型与评估约定 |
| [语义解析实现](reference/semantic-details.md) | Python API、识别规则、时间阈值、探针扩展与限制 |
| [第三方框架](../third_party/README.md) | 固定版本与补丁恢复 |

## 历史材料

以下材料保留当时的证据和设计背景。涉及旧入口、数据路径和测试数字时，以对应版本为限；当前操作以上面的使用指南为准。`data/` 下的本地产物不随 Git 分发，报告中的数据链接可能在新 checkout 中不存在。

| 文档 | 范围 |
| --- | --- |
| [早期实验分析](experiments_analysis.md) | 78 个运行、58 维 v3 特征的结果与泛化限制 |
| [框架完成判定审查](task_completion_audit.md) | 2026-10-03 的完成状态与证据审查 |
| [final 逐项审计](final_task_audit.md) | 2026-10-04 的 96 个目录审计 |
| [逻辑回归验证记录](reference/classification-history.md) | 早期样本、v4 与旧脚本运行记录 |
| [重构验证](reports/refactor-validation.md) | 当次重构的检查结果 |
| [训练实现验证](reports/attribution_training_validation.md) | 包含已被替换的旧分组划分验证 |
| [任务完成报告](reports/TASK_COMPLETION_REPORT.md) | 当次任务交付记录 |
| [语义层原始需求](specs/semantic-actions.md) | 原始设计约束，不是当前目录或命令说明 |
| [整理前总指南](reference/legacy-guide.md) | 旧项目结构的历史快照 |
