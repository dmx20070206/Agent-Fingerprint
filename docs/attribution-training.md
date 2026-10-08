# 训练与评估：四种行为表示

[文档导航](README.md) · 前置：[构建数据集](data-contract.md) · 深入：[实现与评估口径](reference/attribution-details.md)

使用 `attribution` 比较哪些浏览行为能识别 **Agent 框架**或**LLM**。每次训练选择一种输入表示和一个预测目标，主要比较测试集 Macro-F1（各类别 F1 的平均值）。

| 输入表示 | 参数 | 模型看到什么 | 模型 |
| --- | --- | --- | --- |
| 98 维统计 | `--representation stat98` | 点击、按键、滚动等统计量 | XGBoost |
| 原始事件 E | `--representation event` | 事件类型、目标角色、时间间隔 | Transformer |
| 语义内容 Z | `--representation semantic --semantic-mode content` | 动作类型、目标角色 | Transformer |
| 语义内容与时间 Z | `--representation semantic --semantic-mode timing` | 动作类型、目标角色、持续时间与间隔 | Transformer |

`--target agent` 预测框架；`--target llm` 预测模型标签。四种表示 × 两个目标，共八组实验，分别训练。
原有 `train/test` 使用逻辑回归，见[基线说明](classification.md)。

第一次训练建议选择 `stat98 + agent`，确认数据和划分有效后，再比较序列模型。完整顺序是 **dataset → audit → split → train → evaluate → aggregate**；已有数据集和划分时直接训练。各节命令在同一 Bash 终端执行，末尾另有可连续运行的统计模型示例。

## 1. 准备数据

所有命令在仓库根目录执行。已有完整 Conda 环境可跳过安装。

```bash
python -m pip install -e '.[analysis,training]'

# data/runs 中应已有多个框架、模型的真实运行
python -m agent_fingerprint dataset \
  --input-dir data/runs --output-dir data/datasets/phase1

DATASET=data/datasets/phase1/samples.json
SPLIT=data/experiments/phase1/random_split.json

python -m agent_fingerprint attribution audit \
  --dataset "$DATASET" --output data/results/phase1_audit.json
```

先检查 `quality.json` 的纳入/排除原因，以及 audit 的标签分布和序列长度。每个完整 run 是一个样本；源 L3 文件、manifest 和标签必须仍能校验。
至少需要三条有效运行才能划分，训练部分必须覆盖验证/测试中的 Agent 和 LLM 类别；实际分类实验应准备足够的多类别样本。
数据集、划分文件和训练结果使用新路径；重复实验时替换示例中的 `phase1`。

## 2. 固定一次数据划分

```bash
python -m agent_fingerprint attribution split \
  --dataset "$DATASET" --seed 42 \
  --val-fraction 0.2 --test-fraction 0.2 --output "$SPLIT"
```

默认约 60% 训练、20% 验证、20% 测试，按完整 run 随机划分。所有表示和模型种子复用这份文件，方便公平比较。
同一任务或提示词可以出现在不同集合，因此这里评估随机 run 划分下的识别能力，不代表未见任务的泛化能力。
旧版 `attribution-split/v1` 需要重新生成；当前 schema 为 `attribution-split/v2`。

## 3. 训练

先跑一个统计模型：

```bash
python -m agent_fingerprint attribution train \
  --dataset "$DATASET" --split-manifest "$SPLIT" \
  --representation stat98 --target agent \
  --output-dir data/experiments/phase1/stat98_agent
```

再比较三个序列模型，保持同一个目标和划分：

```bash
python -m agent_fingerprint attribution train \
  --dataset "$DATASET" --split-manifest "$SPLIT" \
  --representation event --target agent \
  --output-dir data/experiments/phase1/event_agent

python -m agent_fingerprint attribution train \
  --dataset "$DATASET" --split-manifest "$SPLIT" \
  --representation semantic --semantic-mode content --target agent \
  --output-dir data/experiments/phase1/content_agent

python -m agent_fingerprint attribution train \
  --dataset "$DATASET" --split-manifest "$SPLIT" \
  --representation semantic --semantic-mode timing --target agent \
  --output-dir data/experiments/phase1/timing_agent
```

将 `--target agent` 改成 `--target llm`，同时换输出目录，即可训练另一组。也可加 `--seeds 1 2 3 4 5` 重复训练；结果分别保存在 `seed_1/` 等子目录。

## 常用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--dataset` | 必填 | `dataset` 生成的 `samples.json` |
| `--split-manifest` | 训练必填 | 固定的 train/val/test 划分文件 |
| `--representation` | 必填 | `stat98`、`event` 或 `semantic` |
| `--target` | 必填 | `agent` 或 `llm`，每次一个目标 |
| `--semantic-mode` | `content` | 仅语义路线使用；`timing` 加入时间信息 |
| `--seed` | `42` | 在 split 中控制划分，在 train 中控制模型随机性 |
| `--seeds` | 未指定 | 空格分隔多个模型种子，优先于 `--seed` |
| `--epochs` | `50` | Transformer 最大训练轮数 |
| `--batch-size` | `16` | 每批样本数；显存不足可减小 |
| `--lr` | `0.0001` | Transformer 学习率 |
| `--early-stopping-patience` | `8` | 验证集 Macro-F1 连续不改善的停止阈值 |
| `--max-seq-len` | 训练集长度 P95 向上取整 | 最大 token 数，不含 CLS；超出会截断 |
| `--device` | `cpu` | Transformer 设备；可用 CUDA 环境中可设 `cuda` |
| `--class-weight` | `none` | `balanced` 按训练集类别数量加权 |
| `--xgb-estimators` | `200` | XGBoost 树数 |
| `--xgb-max-depth` | `4` | XGBoost 树的最大深度 |
| `--xgb-learning-rate` | `0.05` | XGBoost 学习率，与 `--lr` 不同 |
| `--threads` | `2` | 训练线程设置 |
| `--output-dir` | 必填 | 本次实验输出位置，使用新目录 |

先设置输入、划分、表示、目标和输出目录，其余保留默认即可。`--epochs/--lr/--batch-size/--device` 用于 Transformer；`--xgb-*` 用于统计模型。切换目标或表示时必须换输出目录。

完整选项：`python -m agent_fingerprint attribution train --help`。
Transformer 仅按验证集 Macro-F1 选择 checkpoint；归一化只使用训练集。XGBoost 的缺失统计值保留为 NaN，输入仍为 98 维。

## 4. 查看、复核和汇总结果

```bash
# 加载已训练模型，重新评估原划分，不重新训练
python -m agent_fingerprint attribution evaluate \
  --dataset "$DATASET" \
  --experiment-dir data/experiments/phase1/timing_agent \
  --output-dir data/results/phase1_timing_reloaded

# 汇总各表示、目标和 seed，生成 tables.md
python -m agent_fingerprint attribution aggregate \
  --input-dir data/experiments/phase1 --output-dir data/results/phase1_summary
```

多 seed 实验的 `--experiment-dir` 应指向具体 `seed_N/` 子目录。

| 输出 | 先看什么 |
| --- | --- |
| `metrics.json` | train/validation/test 的 accuracy、Macro-F1、逐类指标 |
| `predictions.csv`、`confusion_matrix.csv` | 哪些类别容易混淆、哪些运行预测错误 |
| `baselines.json` | 多数类、事件长度、动作长度和动作直方图基线 |
| `training_log.json` | 训练过程与验证表现 |
| `config.json`、`split_manifest.json` | 参数、数据指纹、划分、归一化和截断记录 |
| `checkpoint.pt` / `checkpoint.ubj` | Transformer / XGBoost 模型 |
| 汇总目录的 `tables.md` | 四种表示比较和时间信息消融，多 seed 均值与标准差 |

`aggregate` 按数据集和划分分别汇总，不合并不同 split；缺失实验显示 `--`。

## 可选诊断

```bash
# 小样本拟合检查，用于检查实现；不是正式评估结果
python -m agent_fingerprint attribution overfit \
  --dataset "$DATASET" --target agent --size 24 --unique-content \
  --output-dir data/experiments/phase1/tiny_agent

# 训练模块回归测试，需要 dev 依赖
python -m pytest tests/modeling -q
```

`--unique-content` 仅用于小样本诊断，避免相同内容对应冲突标签；正式训练不会因此筛掉样本。词表、时间处理、PAGE_BOUNDARY 和输入检查说明见[训练实现](reference/attribution-details.md)。

## 完整运行示例：已有采集数据 → 统计模型 → 评估表

前提：已安装 `analysis,training` 依赖，`data/runs` 下有足够的有效运行，且 Agent 分类至少包含两个类别。此示例使用真实已有数据；首页的单条 Mock 样本无法完成分类实验。

在仓库根目录的同一个 Bash 终端执行。每次创建新结果目录，划分、训练和汇总使用相同数据：

```bash
set -e
mkdir -p data/experiments
TRAIN_DIR=$(mktemp -d data/experiments/stat98-demo.XXXXXX)

python -m agent_fingerprint dataset \
  --input-dir data/runs --output-dir "$TRAIN_DIR/dataset"
python -m agent_fingerprint attribution audit \
  --dataset "$TRAIN_DIR/dataset/samples.json" --output "$TRAIN_DIR/audit.json"
python -m agent_fingerprint attribution split \
  --dataset "$TRAIN_DIR/dataset/samples.json" --seed 42 \
  --output "$TRAIN_DIR/split.json"
python -m agent_fingerprint attribution train \
  --dataset "$TRAIN_DIR/dataset/samples.json" --split-manifest "$TRAIN_DIR/split.json" \
  --representation stat98 --target agent --seed 42 \
  --output-dir "$TRAIN_DIR/models/stat98_agent"
python -m agent_fingerprint attribution evaluate \
  --dataset "$TRAIN_DIR/dataset/samples.json" \
  --experiment-dir "$TRAIN_DIR/models/stat98_agent" --output-dir "$TRAIN_DIR/evaluation"
python -m agent_fingerprint attribution aggregate \
  --input-dir "$TRAIN_DIR/models" --output-dir "$TRAIN_DIR/summary"

printf '结果目录：%s\n' "$TRAIN_DIR"
cat "$TRAIN_DIR/summary/tables.md"
```

先看 `dataset/quality.json` 的有效样本数，再看 `models/stat98_agent/metrics.json` 的测试集 Macro-F1。`summary/tables.md` 中未训练的表示和 LLM 目标显示 `--`，属于预期行为；得分取决于你的采集数据。
