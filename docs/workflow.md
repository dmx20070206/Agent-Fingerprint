# 日常工作流

[文档索引](README.md) · 环境准备见[快速开始](quickstart.md)

按需从当前步骤开始：已有运行从“检查与过滤”开始；已有数据集和划分可直接训练。以下命令在仓库根目录、`conda activate agent-fingerprint` 后执行。

## 采集

单次采集，例如 Browser-use + claude 执行订票任务：

```bash
python -m agent_fingerprint collect \
  --agent browseruse --model claude \
  --task-file tasks/flights.jsonl --sandbox-directory sandbox/flights \
  --site flights --timeout 900 --max-steps 100 --no-network-probe
```

默认保存到 `data/runs/<日期>/<run_id>/`。先看 `manifest.json` 的状态和错误，再查 `logs/`；L3 原始事件在 `fingerprints/l3_browser_dynamic.json`。

批量运行使用实验矩阵，先预览，再移除 `--dry-run` 执行：

```bash
python -m agent_fingerprint experiment \
  --agents browseruse,webvoyager --models claude \
  --tasks flights,shop --repeats 3 --dry-run
```

人工采集使用 `python -m agent_fingerprint collect --manual --url /11-mouse-click.html`。更多采集选项、任务路径和排错见[采集参考](reference/collection.md)。

## 检查与过滤

显式选择这一批数据。下面使用已有的 `final`；检查新采集时，将 `RUNS` 改为对应日期目录：

```bash
RUNS=data/runs/final
python scripts/dataset_audit.py --input-dir "$RUNS"
```

终端显示每个 agent + LLM 的收集数量、每个 L3 文件的实际 `len(events)`、任务状态和筛选原因，不生成 Markdown 报告。

```bash
# 预览：任务失败 or L3 < 1000
python scripts/dataset_audit.py --input-dir "$RUNS" --filter --dry-run

# 执行上述过滤，将整条运行转移到隔离目录
python scripts/dataset_audit.py --input-dir "$RUNS" --filter

# 若只按 L3 < 50 筛选，使用这一条替代上面的执行命令
python scripts/dataset_audit.py --input-dir "$RUNS" --filter --l3-only --min-events 50
```

阈值默认 1000，等于阈值保留。任务结果依据 manifest：失败状态、`outcome.success=false` 或页面验证失败均算失败；未知不自动算失败。缺失或损坏的 L3 也会被选中，进行中的运行跳过。记录的成功状态不能代替实际页面证据。

默认隔离到 `<输入目录的祖父目录>/quarantine/<输入目录名>/<时间戳>/`，例如 `data/quarantine/final/...`；可通过 `--quarantine-dir` 指定输入目录外的新目录。转移保留全部文件及相对路径，同步输入根目录的 `index.jsonl`，并在隔离目录保存移出的索引条目和 `moves.json`。恢复时将运行及对应索引条目一并移回。

过滤前暂停对此输入目录的采集或整理。每日入口 `scripts/run_dataset_audit_daily.sh` 自动使用 Conda `agent-fingerprint`，默认只读；找不到 Conda 时通过 `CONDA_EXE` 指定可执行文件。

## 构建数据集

本节的 `attribution audit` 及后续训练需要 `analysis,training` 依赖，安装方式见[环境说明](quickstart.md#常见环境问题)。继续使用上一步的 `RUNS`。每次实验使用新名称；下面统一为 `phase1`，在同一终端继续执行：

```bash
python -m agent_fingerprint dataset \
  --input-dir "$RUNS" --output-dir data/datasets/phase1

DATASET=data/datasets/phase1/samples.json
SPLIT=data/experiments/phase1/random_split.json

python -m agent_fingerprint attribution audit \
  --dataset "$DATASET" --output data/results/phase1_audit.json
```

先看 `quality.json` 中的纳入数量、排除原因和类别分布，再看训练 audit 的标签与序列长度。`dataset_audit.py` 检查原始运行；`attribution audit` 检查生成的训练样本，两者输入不同。

每个完整 run 对应一个样本。`dataset` 已包含统计特征和语义动作提取，无需逐份运行 `extract`。失败但轨迹可用的运行仍可能被 dataset 纳入；仅训练成功任务时，先按上一节过滤。数据格式、标签筛选和目录整理见[数据参考](reference/data.md)。

## 训练与评估

第一次选择 `stat98 + agent`。目标类别至少应有两类，训练集必须覆盖验证和测试中的 Agent、LLM 标签；单条 Mock 记录无法完成分类实验。

```bash
# 固定一次划分，后续表示和模型种子共同使用
python -m agent_fingerprint attribution split \
  --dataset "$DATASET" --seed 42 \
  --val-fraction 0.2 --test-fraction 0.2 --output "$SPLIT"

python -m agent_fingerprint attribution train \
  --dataset "$DATASET" --split-manifest "$SPLIT" \
  --representation stat98 --target agent \
  --output-dir data/experiments/phase1/stat98_agent

# 重载模型复核，不重新训练
python -m agent_fingerprint attribution evaluate \
  --dataset "$DATASET" --experiment-dir data/experiments/phase1/stat98_agent \
  --output-dir data/results/phase1_evaluation

python -m agent_fingerprint attribution aggregate \
  --input-dir data/experiments/phase1 --output-dir data/results/phase1_summary
```

先看 `stat98_agent/metrics.json` 的测试集 Macro-F1，再看 `confusion_matrix.csv` 的类别混淆。汇总表在 `data/results/phase1_summary/tables.md`；未训练的表示显示 `--`。

默认按完整 run 随机划分为约 60% 训练、20% 验证、20% 测试。同一任务可能出现在不同集合，分数不代表未见任务的泛化能力。划分后保留原始数据位置和内容，训练会校验源文件及标签。

## 比较不同表示

保持同一数据集和划分，替换 train 命令中的表示参数，同时换一个输出目录：

| 表示 | 参数 | 模型 |
| --- | --- | --- |
| 98 维行为统计 | `--representation stat98` | XGBoost |
| 原始事件 | `--representation event` | Transformer |
| 语义动作内容 | `--representation semantic --semantic-mode content` | Transformer |
| 语义动作内容与时间 | `--representation semantic --semantic-mode timing` | Transformer |

`--target agent` 预测框架，`--target llm` 预测模型标签。加 `--seeds 1 2 3` 可重复训练，结果各自保存在 `seed_N/`；重载评估时指定具体种子目录。

调参、训练日志和评估口径见[训练参考](reference/training.md)。原有 `train/test` 逻辑回归入口见[基线参考](reference/classification.md)，其参数与 `attribution` 不同。
