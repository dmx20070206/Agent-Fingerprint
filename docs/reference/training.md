# 训练参数与评估口径

[文档导航](../README.md) · 首次训练见[工作流](../workflow.md#训练与评估)。本页集中说明调参、结果文件和实现约定。

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
| `--early-stopping-patience` | `20` | 验证集 Macro-F1 连续不改善的停止阈值 |
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

## 日志与输出

训练进度实时输出到终端（stderr），stdout 保留最终 JSON 结果：

- XGBoost：每个 boosting round 显示当前/总轮数、训练与验证的 log loss 和 acc。
- Transformer：每个 epoch 显示当前/总轮数、训练 loss、训练/验证 acc、验证 Macro-F1、最佳 epoch 和早停计数；结束时显示恢复的最佳 epoch。训练 acc 是该轮训练批次累计的准确率（启用 dropout），验证 acc 在评估模式下计算。

上述指标同时保存在 `training_log.json`；XGBoost 的 `validation_0` 为验证集，`validation_1` 为训练集，acc 等于 `1 - error`（多分类为 `1 - merror`）。CUDA 可用性检测只屏蔽驱动版本过旧的特定 warning；`--device cuda` 的实际运行错误仍正常抛出。

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
  --dataset data/datasets/phase1/samples.json --target agent --size 24 --unique-content \
  --output-dir data/experiments/phase1/tiny_agent

# 训练模块回归测试，需要 dev 依赖
python -m pytest tests/modeling -q
```

`--unique-content` 仅用于小样本诊断，避免相同内容对应冲突标签；正式训练不会因此筛掉样本。词表、时间处理、PAGE_BOUNDARY 和输入检查见下文。

## 数据与模型边界

| 路径 | 模型实际输入 | 模型 |
| --- | --- | --- |
| `stat98` | `features`，严格按 `FEATURE_NAMES` 的 98 维顺序 | XGBClassifier |
| `event` | low-level event type、target role、delta_t value/mask | EventTransformer |
| `semantic --semantic-mode content` | action type、target role | SemanticTransformer |
| `semantic --semantic-mode timing` | action type、target role、duration/gap value/mask | SemanticTransformer |

这里的表示（representation）指模型接收的数据形式，预测目标（target）指要识别的标签。

所有路径独立训练 `--target agent` 或 `--target llm`。不存在组合身份 label、共享模型参数、fusion 或多任务 head。
语义模型直接读取已生成的 `action_sequence_content/temporal`，不会重新执行 E→Z。
统计路径复用 `analysis.representations.matrix` 的严格 schema 校验；JSON null 转为 NaN，交给 XGBoost 原生处理。
没有全量补零或额外缺失指示列，模型输入仍为 98 维。特征重要性保存在 `feature_importance.json`，不代表 disentanglement 结论。

数据源为 `samples.json`，每行同时拥有四种表示；E 从 `source_path` 指向的原始 L3 读取。
Agent/LLM 分别来自 `agent_label/llm_label`，并与 manifest 的 `agent.name/model` 校验。
`run_id` 是完整 trajectory/trial 的样本 ID；旧采集器的 browser `session_id` 实际随页面安装而变化，不参与划分；不把一个 run 拆成多个分类样本。
加载器检查源文件和 manifest 的 SHA-256（若数据集记录了它们）、run ID、标签及语义投影对齐。
四种表示必须都可读取，任何缺失都会显式报错，不按 representation 静默过滤样本。

## 目录与职责

实现位于 `src/agent_fingerprint/modeling/`：

| 文件 | 职责 |
| --- | --- |
| `data.py` | Session/Token、源数据校验、E/Z 白名单投影、数据指纹 |
| `splits.py` | 完整 run 随机划分、类别覆盖检查、manifest 校验 |
| `preprocessing.py` | 固定词表、train-only TimingNormalizer、三类 Dataset、collate |
| `models.py` | 独立实例化的 E/Z Transformer，共用实现 |
| `statistical.py`、`training.py` | XGBoost、随机种子、AdamW/CE、validation Macro-F1 早停、checkpoint |
| `evaluation.py` | 完整分类指标、预测 CSV、混淆矩阵、sanity baselines |
| `inference.py` | 重载已完成实验，在冻结 split 上重新评估，不重新 fit |
| `audit.py`、`overfit.py` | 长度/类别/歧义审计、输入检查、小样本拟合诊断 |
| `aggregate.py`、`cli.py` | 多 seed 表格与统一命令行 |

现有 `analysis/dataset.py` 仅新增分组 ID 和 action document context 的导出；语义动作内容及 98D 不变。
旧数据没有 action context 时仍可训练，PAGE_BOUNDARY 自动关闭。需要可靠边界时，在有明确 document ID 的原始数据上重新运行现有 dataset 命令。

## 随机划分

每个完整 run 是一个样本，按随机种子分配到 train/val/test；不按 task、prompt、browser session、trial 或事件哈希合并样本，不做 Agent/LLM 组件留出实验。
相同任务和提示词可以出现在不同集合，任务模板和实际 prompt 均不限制划分，也不要求提供分组元数据。
数据构建阶段原有的重复 run/事件轨迹检查仍保留。

默认验证集和测试集各占 20%，余下约 60% 为训练集。验证/测试数量按样本数乘比例取整（至少各一条），必须留有训练样本。
使用固定 seed 搜索 512 个随机候选，要求验证/测试中的 Agent 和 LLM 类别在训练中出现，并选择两个标签的类别比例更接近全体数据的候选。
至少需要三条 run；类别覆盖和训练集大小也必须满足要求。

先生成一次 split manifest，所有表示、目标和模型 seed 引用同一文件。生成时 `--seed` 是划分种子，训练时 `--seed/--seeds` 是模型种子。
manifest 保存数据指纹、split ID、完整 run assignment、比例和实际数量；schema 为 `attribution-split/v2`，mode 为 `random`。
旧的 v1 分组划分文件必须重新生成；旧实验文件保留，不自动迁移。
CLI 不再提供 `--split-mode`、`--holdout-agent` 或 `--holdout-llm`。

IID 是 independent and identically distributed（独立同分布）的缩写，并非模型或训练步骤。
本流程使用更直接的名称 random：随机划分允许同类任务同时出现在训练和测试中，不保证轨迹之间统计独立，也不评估未见任务模板的泛化。

## 时间、词表和 padding

E 复用现有事件正规化器的边界识别与结构目标角色映射，不做 pointer/mouse 去重或语义 reduction。
跨 session 的 delta_t 缺失；跨 document 仅在同 session 且双方 epoch 可比较时计算；monotonic clock 仅在相同 document/block 内相减，负值缺失。
保留采集的文档顺序，仅在共同时间轴的 block 内排序，避免将不同页面的 performance.now() 混排。

有效 timing 使用 `log1p(max(x, 0))`，均值和总体标准差只从 train 实际保留的 token 拟合。
无观测或常量列使用 std=1；缺失值编码 value=0、mask=0，真实 0 的 mask=1。
E 的 MLP 接收 `[delta_t_norm, delta_t_mask]`；Z_timing 接收 `[duration_norm, gap_norm, duration_mask, gap_mask]`。
Z_content 根本不建立 timing MLP。配置保存 `duration_mean/std`、`gap_mean/std` 或 `delta_t_mean/std` 以及观测数；验证、测试、重载推理仅 transform。
gap 是现有投影中的 `inter_action_latency_ms`，不称为 reasoning time。

事件、动作、角色使用各自固定且稳定排序的词表，保留 PAD=0、CLS=1、UNK=2。动作与角色分别 embedding，再拼接投影；没有 CLICK_BUTTON 组合 token。
每个序列前加 CLS，learned positional embedding 提供位置；TransformerEncoder 使用 `batch_first=True`。
默认 d_model=128、heads=4、layers=2、FFN=256、dropout=0.1，CLS 经 LayerNorm + Linear 得到单目标 logits。
不同实验分别初始化模型，不共享 E/Z 参数。

batch pad 到当前 batch 最大长度。`padding_mask=True` 表示忽略的 PAD；CLS 永不 mask，空序列只保留 CLS。
`--max-seq-len` 不含 CLS；不指定时取 train 长度 P95 向上取整，至少 1。
每个 split 报告 min/median/mean/P90/P95/P99/max、截断数量/比例、全体及被截断样本的平均丢失 token 数。

PAGE_BOUNDARY 默认自动：仅所有 train action 的 source document ID 可靠时启用，不利用验证/测试来选择此配置。
显式 `--use-page-boundary true` 遇到不可靠训练数据会报错；`false` 可关闭。
边界 token 的两个 timing mask 均为 0；只向模型传入边界标记，document ID 本身不会进入 embedding。

## 输出与解释

每次训练保存 `config.json`、`split_manifest.json`、`checkpoint.pt/ubj`、`training_log.json`、`metrics.json`、`predictions.csv`、`confusion_matrix.csv`、`baselines.json`。
配置包含类别映射、各类别 train/val/test 数量、模型/训练参数、数据与 split 指纹、timing 参数、长度及截断统计、best epoch。
指标包括 accuracy、macro precision/recall/F1、weighted F1、逐类 precision/recall/F1/support 和混淆矩阵；预测文件含 session_id、split、ground_truth、prediction。

sanity baselines 在正式模型前训练：majority、原始 E length、原始 Z length、Z action count+ratio histogram。
后三项使用 StandardScaler + LogisticRegression，scaler 与 classifier 仅 fit train。
length 和 histogram 使用完整轨迹，不包含 PAGE_BOUNDARY/CLS，不自动消除长度信号。

汇总脚本自动生成四行表示比较表和两行 timing ablation 表。
每个 dataset/split 独立展示；不把不同比例或 split 混成 seed。
相同 cell 内参数必须相同，重复 seed 会报错。显示 mean ± population std 和 n；缺失实验显示 `--`。
均值提升或 n=1 的 std=0 不意味着显著性，也不能单靠 sequence 与 histogram 差值认定因果上的 ordering 信息。

验证命令：`python -m pytest tests/modeling -q`，完整回归：`python -m pytest -q`。
旧版分组流程的历史审计、overfit 和回归结果见 [实现验证报告](../archive/attribution_training_validation.md)。
