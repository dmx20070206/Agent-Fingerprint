# 逻辑回归历史验证记录

以下是旧版本的实验与测试记录，特征维度、路径和测试状态仅对应当时版本。当前操作见[逻辑回归基线](../classification.md)，其他历史材料见[文档导航](../README.md)。

## 2026-10-05 验证记录

执行 `bash scripts/run_analysis.sh all --run-name final_20261005`：

- 164 个运行目录改为 `run_0001` 至 `run_0164`，同步 984 个 JSON 文件，清理 1 个空目录。
- 新旧名称映射：`data/runs/final/run_names.json`；整理报告：`data/results/prepare_final_20261005.json`。
- 数据集：`data/datasets/l3_final_20261005/`，纳入 161 次运行；3 次运行因交互事件为空排除，详见 `quality.json`。
- Agent 模型：`data/experiments/l3_final_20261005_agent/`，五折 accuracy=1.0000、macro-F1=1.0000。
- LLM 模型：`data/experiments/l3_final_20261005_llm/`，五折 accuracy≈0.6211、macro-F1≈0.5462。
- 离线分析回归测试 24 项通过。上述指标是当前数据上的交叉验证结果，不是独立留出测试集结果。

## 早期验证记录（29 次运行）

本次构建扫描到 29 个运行，全部纳入，Agent 分布为 5/6/6/6/6。
Agent 五折折外 accuracy=1.0、macro-F1=1.0；多数类基线 accuracy≈0.1724。
这是少量同任务数据上的流程验证，不代表跨任务或跨模型的泛化能力。
LLM 只有 chat-gpt 一个类别，按设计跳过。相关测试 14 项通过。
全仓库 pytest 在收集 test/test_migrate_runs.py 时因现有模块
scripts.migrate_runs_v1_to_v2 缺失而中止；不是本次分类测试失败。

## v4 离散位置特征验证（2026-10-05）

执行 dataset/train --run-name transitions_v4_20261005，从现有 raw 重建 159 条、67 维样本，
未重新采集。数据集位于 `data/datasets/l3_transitions_v4_20261005/`，
模型及交叉验证报告位于 `data/experiments/l3_transitions_v4_20261005_agent/` 和
`data/experiments/l3_transitions_v4_20261005_llm/`，历史产物保留。
Agent 交叉验证 accuracy=1.0000、macro-F1=1.0000；
LLM accuracy≈0.6792、macro-F1≈0.5794。这些不是独立留出测试指标。
离线分析回归测试 29 项通过。run_0052 的 11 个移动点产生 10 次跳转、9 个转向角；
平均跳转距离≈239.55 CSS px，平均事件间隔≈3151.55 ms，孤立移动占比为 1。
37 条无移动事件的样本仍保持新增移动特征为 null。
