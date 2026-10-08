# 逻辑回归基线：train / test / predict

[文档导航](README.md) · 当前四种表示实验：[attribution 训练指南](attribution-training.md)

本页用于原有逻辑回归流程。两种输入分别是 98 维统计特征（`statistics`）和 162 维语义统计（`semantic`）。每种输入分别预测 Agent 与 LLM，默认训练四个模型。
语义统计由动作计数、目标计数、相邻动作转移和时间统计组成；这里不训练 Transformer。

| 目的 | 命令 | 结果 |
| --- | --- | --- |
| 交叉验证并保存模型 | `train` | evaluation.json、model.joblib |
| 评估独立数据 | `test` | 独立测试报告 |
| 预测单份轨迹 | `predict` | Agent 或 LLM 标签 |
| 重新绘制评估图 | `plot` | PNG / SVG 图表 |

## 逐步运行

先按[安装说明](setup.md)安装 `analysis` 依赖，并准备足够的多类别运行。以下在仓库根目录执行，输出目录需为空。

```bash
# 1. 构建数据集
python -m agent_fingerprint dataset \
  --input-dir data/runs --output-dir data/datasets/baseline

# 2. 默认训练两种表示 × 两个目标
python -m agent_fingerprint train \
  --dataset data/datasets/baseline/samples.json \
  --output-dir data/experiments/baseline

# 3. 用保存的统计模型预测一份原始 L3（替换为实际文件）
python -m agent_fingerprint predict \
  --model data/experiments/baseline/statistics/agent/model.joblib \
  --input path/to/run/fingerprints/l3_browser_dynamic.json

# 4. 用另一批无训练样本重叠的运行做独立评估
python -m agent_fingerprint dataset \
  --input-dir data/test_runs --output-dir data/datasets/baseline_test
python -m agent_fingerprint test \
  --dataset data/datasets/baseline_test/samples.json \
  --model-dir data/experiments/baseline \
  --output-dir data/results/baseline_test
```

`path/to/...` 是占位路径；`data/test_runs` 需自行准备独立数据。训练数据上的 predict 输出不算独立测试成绩。

## 常用参数

| 命令 / 参数 | 默认值 / 说明 |
| --- | --- |
| `train --dataset` | 建议显式指定；省略时读取 `configs/classification.yaml` 的 dataset |
| `train --target` | `both`；也可只选 `agent` 或 `llm` |
| `train --representation` | `both`；也可只选 `statistics` 或 `semantic` |
| `train --config` | 默认 `configs/classification.yaml`，设置折数、种子和正则参数 |
| `dataset/train --sites` | 网页标签，例 `flights,shop` |
| `dataset/train --agents` | 框架标签，例 `browseruse,skyvern` |
| `dataset/train --llms` | 模型标签，例 `chat-gpt,deepseek-chat` |
| `test --model-dir` | 保存模型的目录，需与训练的表示/目标层级一致 |
| `test --target/--representation` | 与训练选择一致；默认均为 `both` |
| `predict --model` | 单个 `model.joblib` 路径 |
| `predict --input` | 原始 L3 JSON 路径 |

筛选参数为逗号分隔；省略或 `all` 表示全选，同一维度取并集，不同维度取交集。
训练配置默认 `folds: 5`、`seed: 42`、`regularization_c: 1.0`；C 越小，正则约束越强。

## 模型究竟存在哪里

Python 入口只为值为 `both` 的维度添加子目录：

| train 选择 | 模型位置（相对于 `--output-dir`） |
| --- | --- |
| 两项均为 `both` | `<statistics或semantic>/<agent或llm>/model.joblib` |
| `--representation statistics --target both` | `<agent或llm>/model.joblib` |
| `--representation both --target agent` | `<statistics或semantic>/model.joblib` |
| `--representation statistics --target agent` | `model.joblib` |

每个模型目录保存配置、输入快照、`evaluation.json`、模型及 PNG/SVG 评估图。样本不足时保存 skipped 原因，不生成模型。

## 如何理解评估

`train` 先按目标标签分层做交叉验证，再用全量样本拟合部署模型。缺失填补、缺失指示与标准化都在训练折内拟合。
少数类不足 5 条时减少折数；少于两类或任一类少于两条时跳过训练。
`evaluation.json` 是折外结果；`test` 是独立数据评估，会拒绝 run ID、原始文件哈希或事件哈希与训练样本重叠。
同一任务可能出现在不同交叉验证折，不能据此推断跨任务泛化能力。

## 绘制已有模型的评估图

`--model-dir` 指向包含 `evaluation.json` 的单个模型目录：

```bash
python -m agent_fingerprint plot \
  --model-dir data/experiments/baseline/statistics/agent
```

## Shell 快捷入口

```bash
# 构建数据集、训练基线、运行离线回归测试；不会整理原始运行目录
bash scripts/run_analysis.sh all --input-dir data/runs --run-name baseline_shell

# 仅生成已有模型的评估图
bash scripts/run_analysis.sh plot --run-name baseline_shell
bash scripts/run_analysis.sh --help
```

脚本的模型路径是 `data/experiments/l3_<run-name>_<agent或llm>/<statistics或semantic>/`，与 Python 默认目录层级不同；分步调用时沿用同一个入口及 `--run-name`。
脚本 `prepare` 会实际整理，预览需加 `--dry-run`；Python `prepare` 默认预览，执行需加 `--apply`，见[数据整理](data-contract.md)。

```bash
python -m pytest tests/test_classification.py tests/test_dual_classification.py -q
```

早期实验数字和旧脚本记录已移至[历史验证](reference/classification-history.md)，不作为当前运行结果。
