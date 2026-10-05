# L3 离线分类流程

流程：`data/runs/final → analysis.dataset → samples.json → analysis.train → 模型和评估`。
一行样本对应一次完整运行。Agent 和 LLM 使用同一份 L3 特征，以 `--target` 切换标签。
采集代码无需依赖机器学习库；离线依赖单独安装。

精选数据目录为 `data/runs/final/<任务>/<agent>/<模型>/<run_id>/`，例如
`flights/browseruse/chat-gpt/run_0045/`。末级目录使用全局唯一的 `run_0001` 格式编号，
结构化 `run_id` 与目录名一致。新增运行从当前最大编号继续分配，重复执行保留现有编号。
`final/run_names.json` 记录新旧路径和 ID，`final/index.jsonl` 记录新 ID 的路径。
分析命令支持递归查找，也可将 `--input-dir` 指向任务、Agent 或模型子目录。

推荐使用统一 Bash 入口，支持平铺目录和已按任务/Agent/模型分组的目录：

```bash
# 只预览；不改运行数据，另写审计报告
bash scripts/run_analysis.sh prepare --dry-run
# 执行归档、简化目录、同步内部 run_id、更新索引、清理空的运行目录
bash scripts/run_analysis.sh prepare
# 一键完成整理、特征提取、Agent/LLM 训练与交叉验证、离线回归测试
bash scripts/run_analysis.sh all

# 分步运行：相同 run-name 关联同一组数据集和模型
bash scripts/run_analysis.sh dataset --run-name experiment_01
bash scripts/run_analysis.sh train --run-name experiment_01
bash scripts/run_analysis.sh test
# 使用保存的模型预测一份原始 L3 文件
bash scripts/run_analysis.sh predict --run-name experiment_01 --predict-target agent \
  --predict-input data/runs/final/flights/browseruse/chat-gpt/run_0045/fingerprints/l3_browser_dynamic.json
# 仅训练一种标签；输出目录需为空
bash scripts/run_analysis.sh train --run-name experiment_02 --target agent \
  --dataset-dir data/datasets/l3_experiment_01

# 全仓库测试（需要额外安装采集依赖）
bash scripts/run_analysis.sh test --full
bash scripts/run_analysis.sh --help
```

### 按网页集合、Agent、LLM 筛选

`all`、`dataset`、`train` 支持 `--sites`、`--agents`、`--llms`，
每个参数使用逗号分隔多个名字；省略或指定 `all` 表示该维度全选。
同一维度为多选，不同维度取交集。名字精确匹配：网页集合使用归档目录名
（如 `flights`、`forum`、`shop`），Agent/LLM 使用 manifest 中的 name/model。
`--target` 只决定预测 Agent 还是 LLM，与样本筛选独立。

```bash
# 仅 flights 网页集合，Agent 与 LLM 全选
bash scripts/run_analysis.sh all --sites flights --run-name flights_only

# 选择一个网页集合、两个 Agent、两个 LLM，构建并训练评估
bash scripts/run_analysis.sh all --sites flights \
  --agents browseruse,skyvern --llms chat-gpt,deepseek-chat --run-name flights_subset

# 从 raw 构建筛选数据集，随后训练，无需重复筛选参数
bash scripts/run_analysis.sh dataset --sites flights,shop --run-name two_sites
bash scripts/run_analysis.sh train --run-name two_sites

# 也可直接筛选已有数据集；原数据集不变，模型目录保存所选样本快照
bash scripts/run_analysis.sh train --dataset-dir data/datasets/l3_transitions_v4_20261005 \
  --sites flights --agents browseruse,skyvern --run-name flights_retrain
```

`quality.json` 记录筛选条件、网页集合数量和 `filtered_out`（未选中的运行），
与质量不合格的 `excluded` 分开。未知名字或筛选后无可用样本会报错，
仅一个类别或样本不足时训练仍按现有规则生成 skipped 报告。
Python 入口 `analysis.dataset` 和 `analysis.train` 同样支持这三个参数。
旧数据集缺少 `site_label` 时，训练可从归档格式的 `source_path` 推断网页集合；
无法识别时请先从已归档 raw 重建数据集。
`all` 的 prepare 步骤仍整理整个 input-dir，筛选在构建数据集时生效。
`test` 是代码回归测试，不接受数据筛选；分类交叉验证请使用 `train` 或 `all`。

脚本可以从任意工作目录调用；相对路径统一按仓库根目录解析。通过 `PYTHON=/path/to/python`
选择解释器。默认生成带 UTC 时间和进程号的新输出目录，也可用 `--dataset-dir`、
`--agent-model-dir`、`--llm-model-dir` 指定路径。现有非空结果目录拒绝覆盖。
准备步骤的审计报告默认保存在 `data/results/prepare_<run-name>.json`，可用 `--report` 自定义。
`--index data/runs/index.jsonl` 可将索引更新写入全局运行索引，默认写入输入根目录。
`prepare --dry-run` 是只读预览；`prepare` 和 `all` 会实际修改运行目录与 ID。
`test` 指代码回归测试；分类器测试指标来自 `train` 内的分层交叉验证，保存在 `evaluation.json`。

旧的 `python -m analysis.organize_runs --apply` 仍只做分层归档并保留长目录名；
统一整理请使用 `prepare`。

整理后若已有数据集包含旧的 `source_path`，应重新构建数据集；升级特征 schema 后，历史模型需要重新训练才能用于预测。

## 文件职责

| 文件 | 职责 |
|---|---|
| `analysis/feature_schema.py` | v5 特征名称、顺序和数量的唯一契约；共 98 维 |
| `analysis/l3_browser_dynamic.py` | 单次原始事件的纯特征提取、会话与轨迹分段 |
| `analysis/_common.py` | 原有单次提取命令的路径解析和 JSON 读写 |
| `analysis/dataset.py` | 递归扫描 manifest，校验身份、标签、事件，去除重复轨迹，生成特征表及质量报告 |
| `analysis/evaluate.py` | 按 run 分层交叉验证、折外预测、多数类基线、指标和划分记录 |
| `analysis/train.py` | 读取数据及配置，组装缺失填补→标准化→逻辑回归，评估并保存全量拟合模型 |
| `analysis/predict.py` | 使用保存的特征契约和预处理，对一份原始 L3 文件预测 |
| `analysis/organize_runs.py` | 预览或执行任务/Agent/模型分层归档，并更新运行索引 |
| `analysis/prepare_runs.py` | 统一归档、全局短编号、ID 同步、新旧路径映射和索引更新 |
| `scripts/run_analysis.sh` | 整理、数据集、训练、测试、预测的 Bash 入口 |
| `analysis/normalize_run_ids.py` | 预览或修复 run 目录内 JSON/JSONL 的结构化 run_id 字段 |
| `config/classification.yaml` | 数据集路径、折数、随机种子、逻辑回归 C 参数 |
| `requirements-analysis.txt` | 独立分析依赖 |
| `test/test_l3_browser_dynamic.py` | 特征语义与边界回归测试 |
| `test/test_classification.py` | 数据质量、ID 修复、训练隔离、保存加载、单类跳过测试 |

## 安装与运行

以下命令均在仓库根目录执行：

```bash
python -m pip install -r requirements-analysis.txt

# 可选：只提取某一次运行
python -m analysis.l3_browser_dynamic --task-id run_0045 --input-dir data/runs/final --output-dir data/results

# 构建数据集
python -m analysis.dataset --input-dir data/runs/final --output-dir data/datasets/l3_v4

# Agent 分类
python -m analysis.train --target agent --output-dir data/experiments/l3_v4_agent

# LLM 分类（输入只有一种 LLM 时保存 skipped 报告，不生成模型）
python -m analysis.train --target llm --output-dir data/experiments/l3_v4_llm

# 预测；以下已有样本仅用于演示接口，不能当作独立测试集
python -m analysis.predict --model data/experiments/l3_v4_agent/model.joblib --input data/runs/final/flights/browseruse/chat-gpt/run_0045/fingerprints/l3_browser_dynamic.json
```

数据集与实验目录拒绝覆盖非空目录。上述默认目录如果已经存在，重新实验请使用新目录：

```bash
python -m analysis.dataset --output-dir data/datasets/l3_v4_run2
python -m analysis.train --dataset data/datasets/l3_v4_run2/samples.json --target agent --output-dir data/experiments/l3_v4_agent_run2
```

修改 YAML 可以调整折数、种子和 C；也可用 `--config` 指定另一份配置。默认最多 5 折；少数类不足 5 个时自动降低折数。少于两个类别或任一类少于两个样本，保存跳过原因。

## 产物与评估含义

- 数据集 `samples.json`：schema、固定特征列以及每个样本的特征、标签、路径、原始文件哈希。缺失特征使用 JSON `null`。
- 数据集 `quality.json`：纳入数量、排除原因、警告、类别分布、每列缺失数量。空轨迹不会伪装成全零训练样本。失败状态但有可用交互轨迹的运行保留并警告。
- 实验 `config.json`：实际参数、数据集哈希、特征版本及 Python/NumPy/sklearn 版本。
- 实验 `samples.json`：训练输入快照。
- 实验 `evaluation.json`：每折训练/测试样本 ID、折外逐样本预测、accuracy、macro-F1、分类报告和混淆矩阵。矩阵行为真实类别，列为预测类别，顺序见 labels。
- 实验 `model.joblib`：评估完成后使用全部可用样本重新拟合的 Pipeline、标签类别、特征契约。预测沿用训练时的填补和缩放。仅加载自己信任的模型文件。

中位数填补、缺失指示列和标准化在每个训练折内拟合；全缺失列保留并填零。输入固定为 98 维，加入缺失指示后分类器内部维度可能增加。run_id、目录、状态、标签等元数据不会进入 X。

当前验证衡量当前数据分布下的新 run 区分能力。本入口只实现按 run 的分层评估，并不保证测试折的任务、模型或采集批次与训练折隔离；如需验证这些泛化能力，需要另设按任务/模型/批次留出的实验。不要将同一 run 切成多个样本后直接使用本入口。
任务标识优先使用 manifest.task.task_id；没有时对 requested_url 和 prompt 生成稳定哈希，不把随机端口纳入任务标识。模型标签读取 manifest.agent.model；代理别名的真实模型版本应在采集时另外记录。

## ID 修复

目录名和内部运行 ID 统一由 `prepare` 完成；若只想同步 ID 而保留目录名称，可单独使用下述命令。
脚本只改结构化 run_id，不改自由文本日志、事件 session_id、语义任务标识 task.task_id，也不改 final 外的原始历史运行。CLI 中的 --task-id 对应此处的运行 run_id。

```bash
# 预览，完成修复后应输出 []
python -m analysis.normalize_run_ids --input-dir data/runs/final
# 将来需要修复新的数据时
python -m analysis.normalize_run_ids --input-dir data/runs/final --apply --report data/results/new_id_repair.json
```

## 测试

```bash
python -m pytest test/test_l3_browser_dynamic.py test/test_classification.py -q
# 仓库完整测试（同时依赖原有 requirements.txt）
python -m pytest -q
```

机器核心数较多时，可在测试或训练命令前加 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1`，避免底层库启动过多线程。

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

## 结果图

训练完成后，每个模型目录自动生成 `evaluation.png` 和 `evaluation.svg`。
图中包含总体 accuracy、macro-F1、混淆矩阵（数量和真实类别内百分比）、
各类别 precision/recall/F1 及样本数。指标来自交叉验证折外预测。
PNG 可直接预览，SVG 适合放大或导入论文、报告。JSON 和终端不再输出基线指标。

已有实验无需重新训练即可绘图：

```bash
bash scripts/run_analysis.sh plot --run-name exp01
# 仅绘制 LLM 结果
bash scripts/run_analysis.sh plot --run-name exp01 --target llm
```

`plot` 只读取已有评估文件并更新图像；也兼容带旧基线字段的历史报告。

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
