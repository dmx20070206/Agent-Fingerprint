# 文档索引

[项目首页](../README.md)

日常只需两篇：

| 文档 | 解决什么问题 |
| --- | --- |
| [快速开始](quickstart.md) | 使用 Conda 环境，验证本地流程，准备真实 Agent |
| [日常工作流](workflow.md) | 采集 → 检查与过滤 → 数据集 → 训练与评估 |

## 按需查询

| 参考 | 内容 |
| --- | --- |
| [命令速查](reference/commands.md) | 入口选择、容易混淆的参数 |
| [采集](reference/collection.md) | 参数、任务文件、批量配置、运行排错 |
| [本地模型](reference/local-models.md) | vLLM 权重、服务与网关连接 |
| [数据](reference/data.md) | 文件格式、标签、质量规则、目录整理与迁移 |
| [训练](reference/training.md) | 超参数、结果文件、划分与评估实现 |
| [统计特征](reference/features.md) | 98 维特征定义与计算口径 |
| [语义动作](reference/semantic-actions.md) | 单文件提取、Python API、解析规则 |
| [架构与配置](reference/architecture.md) | 代码职责、配置文件与执行边界 |
| [逻辑回归基线](reference/classification.md) | 原有 train / test / predict 入口 |

历史实验、审计报告、修复记录及旧设计统一保存在[归档](archive/README.md)。

文档维护约定：常用操作只在工作流介绍一次；参数和实现放在 `reference/`；有日期和版本范围的结果放在 `archive/`。新增内容先合并到对应页面，避免再增加并列入门指南。
