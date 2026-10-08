# 第一阶段训练实现与验证（2026-10-08）

[文档导航](../README.md) · **历史验证记录**：包含旧版分组划分，不作为当前随机 run 划分的操作说明。当前命令见[训练指南](../attribution-training.md)。

## 交付状态

四种 representation × 两个单独分类目标的训练、checkpoint 重载评估、统一分组 split、sanity baselines 和自动表格已实现。
真实数据完成审计及六条 Transformer tiny-overfit 诊断；正式八组 held-out 指标尚不能在当前数据上合法产生。
原因是所有 159 条运行只有两个 prompt/task 连通分组，不能满足任务要求的三个非空且 prompt 不重叠集合。
split 命令已实际运行，明确报错退出，没有生成不合格划分，也没有退回样本随机 split。

完整使用说明和可执行命令见 [训练与评估](../attribution-training.md)。

## 变更清单

新增 `src/agent_fingerprint/modeling/`：

- `data.py`：多表示 Session、事件/语义投影、源文件/标签校验与数据指纹。
- `splits.py`：身份/重复轨迹/task/prompt 连通组，四种 split 与加载复核。
- `preprocessing.py`：固定词表、TimingNormalizer、StatisticalDataset、EventSequenceDataset、SemanticActionDataset、padding。
- `models.py`：共享架构的 EventTransformer / SemanticTransformer，模型参数独立。
- `statistical.py`：固定 98D XGBoost 与 feature importance。
- `training.py`：单目标训练、随机种子、AdamW/CE、validation Macro-F1 早停与 checkpoint。
- `evaluation.py`：完整指标、分类预测、混淆矩阵、majority/length/histogram baselines。
- `inference.py`：保存模型的冻结 split 重载评估，不重新 fit。
- `audit.py`、`overfit.py`：数据/输入检查、内容标签歧义诊断及 tiny-overfit。
- `aggregate.py`、`cli.py`、`__init__.py`：多 seed 表格、统一入口及包定义。

新增 `tests/modeling/` 下 conftest 与数据/split、序列、训练集成测试，共 31 项。
新增 `docs/attribution-training.md` 与本报告。
修改 `analysis/dataset.py`：仅增加结构上下文与分组 ID 的导出；修改 `cli/main.py` 增加 `attribution` 命令。
修改 `pyproject.toml` 与 `environment.yml` 增加 training 依赖；README 和分类文档增加新流程入口说明。
这些修改基于原有工作区重构，没有恢复旧目录、覆盖其他已存在的改动，或修改 feature schema、E→Z 规则。

## 当前数据检查

- 98D：`data/datasets/l3_exp02/samples.json` 的 `features`，v5 schema 在 `src/agent_fingerprint/features/feature_schema.py`。
- E：`data/runs/final/**/fingerprints/l3_browser_dynamic.json`，由 `source_path` 引用。
- Z：同一 samples.json 的 `action_sequence_content/temporal`；不重新计算语义动作。
- Agent：5 类，计数 37/23/35/35/29；LLM：4 类，计数 48/20/48/43。
- 运行单位为 manifest 的 `run_id`；browser session ID 作为额外分组。
- 原始 manifest 没有显式 task_id/prompt_id/trial_id/session_id；实际 prompt 哈希和现有 task_label 形成 82、77 条的两组。
- 原始事件没有可靠 document_id，因此 PAGE_BOUNDARY 自动关闭。
- 历史训练为 Logistic Regression + StratifiedKFold；新研究流程不使用其样本级随机划分。

| 序列 | min | median | mean | P90 | P95 | P99 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E | 51 | 221 | 339.99 | 828 | 904 | 1056.30 | 1247 |
| Z | 6 | 23 | 24.07 | 41 | 45 | 50.52 | 67 |

审计产物：[data_audit.json](../../data/experiments/attribution_v1/data_audit.json)。
这些全量统计用于数据描述；正式训练默认长度只从 train P95 确定，不从这些全量数字设置。

## 数据流与隔离

98D 严格按 schema 排序并保留 null→NaN，经 XGBoost 原生缺失值路径；不扩展维度。
E 仅使用 event type、target role、delta_t；Z_content 仅 action type、target role；Z_timing 再加入 duration 与 inter-action latency。
CSS/DOM ID、文本、URL、轨迹坐标、输入次数等不会混入 Z。所有标签/元数据仅用于监督或分组。

统一 split manifest 按 run/session/trial、重复 trace、task/prompt 连接关系整体划分。
每条路径加载同一 manifest 并校验数据指纹与 split checksum。支持 iid、unseen_pair、leave_one_llm_out、leave_one_agent_out。
后两者分别只允许 Agent、LLM 分类；unseen_pair 要求训练分别见过两个组件。与 holdout 共享泄漏组的其他样本明确 quarantined 为 excluded。

有效 timing 经 log1p 后，只在 train 拟合均值与标准差；缺失与真实零通过 mask 区分。
val/test 和 checkpoint 重载仅使用保存的统计。batch 动态 padding，PAD attention mask=True，CLS=False；空序列保留 CLS。
默认 max_seq_len 为训练长度 P95，所有截断数量、比例、丢失 token 数都保存。

## Unit / integration 验证

- `python -m pytest tests/modeling -q`：**31 passed**。
- `python -m pytest -q`：**270 passed，8 skipped，20 subtests passed**。
- `ruff check src/agent_fingerprint/modeling tests/modeling`：通过。
- `compileall` 和 CLI help：通过。

覆盖固定 98D 顺序、稳定 event/action/role 词表、UNK、null/零 mask、train-only timing、冻结预处理、动态 padding、attention mask、CLS、PAGE_BOUNDARY、三种序列 forward、batch>1、单 token/空序列、截断统计、checkpoint 重载预测一致、seed 稳定 split、四种表示共用 assignment、任务/提示词/重复导出传递分组、组件留出及隔离、源哈希变更拒绝、validation Macro-F1 最优权重恢复。
八条模型路径都完成了合成 fixture 上的短训练、完整指标导出和保存模型重载复核；CLI split/train/inspect/aggregate、多 seed 聚合与重复 seed 拒绝均已验证。

自动生成的八格表格示例位于 [synthetic tables](../../data/experiments/attribution_v1/synthetic_validation/test_all_eight_paths_export_co0/summary/tables.md)。
**该表来自测试 fixture，仅验证管线和自动汇总，不能作为真实数据的论文结果。**

## Tiny overfit：真实运行

先按各 Agent 较短运行轮流选取 24 条，保留重复语义内容。
E 和 Z_timing 达到 100%，Z_content 达到 91.67%，loss=0.17545。
检查发现同一内容序列在这批数据中出现 7 次，其中 2 次标签为 Agent-E，5 次为 AutoGen；确定性 Z_content 分类器不能区分这些输入。
这批样本的理论经验 accuracy 上限正好是 22/24=91.67%；首次报告保留于 [overfit_agent](../../data/experiments/attribution_v1/overfit_agent/overfit_report.json)。

随后使用显式 `--unique-content` 选取 24 条不同语义内容的真实运行，只用于实现诊断；不修改正式数据集。
两个目标分别执行，E/Z_content/Z_timing 在每个目标内部使用相同的 24 条运行。

| 模型 | Agent 训练 loss / epoch | LLM 训练 loss / epoch | 训练 accuracy |
| --- | --- | --- | --- |
| E | 0.04780 / 16 | 0.04892 / 29 | 均为 1.000 |
| Z_content | 0.04360 / 18 | 0.03195 / 32 | 均为 1.000 |
| Z_timing | 0.03436 / 22 | 0.04144 / 25 | 均为 1.000 |

通过标准为训练 CE < 0.05 且 accuracy=1，六个诊断均通过。
配置为 d_model=64、heads=4、layers=2、FFN=128、dropout=0、lr=0.003、weight_decay=0、seed=42，无截断。
产物含每 epoch loss、session ID、实际 preprocessing、model config 与 checkpoint：

- [Agent report](../../data/experiments/attribution_v1/overfit_unique_agent/overfit_report.json)
- [LLM report](../../data/experiments/attribution_v1/overfit_unique_llm/overfit_report.json)

这些都是训练集拟合诊断，不是 held-out accuracy/F1，也不说明泛化能力。

## 正式结果的剩余数据前提

需要补充独立 task/prompt 实例，使至少三个连通组可以分配到 train/val/test，并保证训练类覆盖。
组件留出还要求去除与 held-out 样本关联的泄漏组后，仍有足够训练/验证数据和分别出现的组件。
新增 trial ID 或改写现有 prompt ID 不会解决实际重复提示词分组的问题。
数据满足条件后，使用文档中的 split → 四种表示 × 两目标 train → aggregate 命令生成正式比较表、timing ablation 和 component generalization 表即可。
本轮未增加 fusion、adversarial、multi-task 或复杂超参数搜索。
