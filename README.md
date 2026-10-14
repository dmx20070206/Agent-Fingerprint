# Agent-Fingerprint

记录浏览器 Agent 的点击、输入、滚动和模型调用行为，研究这些行为能否识别 **Agent 框架**或**底层 LLM**。

支持 Browser-use、WebVoyager、Skyvern、AutoGen、Agent-E，也支持人工操作和本地 Mock。每次完整运行作为一个样本，可比较统计特征、原始事件和语义动作三类表示。

```text
采集运行 → 检查与过滤 → 构建数据集 → 训练与评估
```

## 开始使用

在仓库根目录，使用已有的 Conda 环境：

```bash
conda activate agent-fingerprint
python scripts/dataset_audit.py --input-dir data/runs/final
```

这条命令只查看组合数量、L3 条数和任务状态。已有数据可直接进入工作流的检查步骤。

| 你的起点 | 阅读入口 |
| --- | --- |
| 第一次使用，确认环境和流程可用 | [快速开始](docs/quickstart.md) |
| 采集、筛选数据，完成一次训练 | [日常工作流](docs/workflow.md) |
| 查参数、格式、实现或历史结果 | [文档索引](docs/README.md) |

实现位于 `src/agent_fingerprint/`，配置位于 `configs/`，任务和测试网页位于 `tasks/`、`sandbox/`。采集与实验产物保存在 `data/`，不纳入 Git。
