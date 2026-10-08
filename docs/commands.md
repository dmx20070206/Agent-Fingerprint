# 命令与参数速查

[文档导航](README.md) · [首次运行示例](../README.md#完整运行示例本地采集--提取--数据集)

以下命令在仓库根目录、已激活的主环境中执行。`af` 等价于 `python -m agent_fingerprint`。表中的 `<命令>` 等尖括号内容是占位符，执行前需替换。

## 按目的选命令

| 目的 | 入口 | 输入 → 输出 | 详细用法 |
| --- | --- | --- | --- |
| 跑一次任务 | `collect` | 任务 / URL → 原始运行目录 | [采集](collection.md) |
| 跑多组组合 | `experiment` | 实验 YAML → 多次 collect 与报告 | [批量采集](collection.md#批量实验) |
| 查看一份轨迹 | `extract` | L3 JSON → 98 维统计与语义动作 | [提取](semantic-actions.md) |
| 准备训练数据 | `dataset` | 运行目录 → samples.json、quality.json | [数据](data-contract.md) |
| 检查数据能否训练 | `attribution audit` | samples.json → 标签与长度报告 | [训练](attribution-training.md) |
| 固定数据划分 | `attribution split` | samples.json → 划分文件 | [训练](attribution-training.md) |
| 训练四种表示之一 | `attribution train` | 数据集 + 划分 → 模型、指标 | [训练](attribution-training.md) |
| 重载模型复核 | `attribution evaluate` | 已训练实验 → 原划分上的指标 | [训练](attribution-training.md) |
| 汇总多组实验 | `attribution aggregate` | 实验根目录 → tables.md | [训练](attribution-training.md) |
| 诊断序列训练 | `attribution overfit / inspect` | 小样本拟合 / 检查模型输入 | [实现参考](reference/attribution-details.md) |
| 使用逻辑回归 | `train / test / predict / plot` | 特征 → 基线模型、预测与图表 | [基线](classification.md) |
| 整理运行目录 | `prepare` | 运行目录 → 整理计划；加 --apply 执行 | [整理](data-contract.md#整理与修复按需执行) |
| 单独整理或修复 | `organize / normalize / migrate` | 目录归类 / ID 同步 / v1 迁移 | [整理](data-contract.md#整理与修复按需执行) |

通常只需 `collect → dataset → attribution split → attribution train`。单份提取、目录整理和历史格式迁移都按需使用。

## 容易混淆的参数

| 参数 | 用在哪 | 含义与写法 |
| --- | --- | --- |
| `--agent` | collect | 一个执行框架，如 browseruse；Shell 目录名 browser-use 不是此参数值 |
| `--agents` | experiment | 选择实验框架，逗号分隔，如 browseruse,skyvern |
| `--agents` | dataset / train | 按已采集的框架标签筛选样本 |
| `--model` | collect | 模型路由别名，如 claude；上游版本由网关配置决定 |
| `--model` | predict | 已保存的 model.joblib 文件路径 |
| `--models` / `--llms` | experiment / dataset、train | 前者选择要运行的模型；后者筛选已有模型标签 |
| `--tasks forums` / `--sites forum` | experiment / dataset、train | 论坛任务名是复数 forums，数据标签是单数 forum |
| `--task-id` | collect | 任务身份；重复执行同一任务时可以相同 |
| `--task-id` | extract | 历史参数名，实际查找 run ID；建议改用 --input-file |
| `--output-root` / `--output-dir` | collect | 前者自动生成多次运行目录；后者固定一个运行目录 |
| `--input-file` / `--input-dir` | extract / dataset | 单份原始 JSON / 递归扫描的运行目录 |
| `--seed` | attribution split / train | 划分随机种子 / 模型随机种子，两者独立 |
| `--seeds 1 2 3` | attribution train | 多个模型种子用空格分隔，每个种子单独保存 |
| `--dry-run` / `--apply` | experiment / prepare | 前者预览实验；后者执行整理。prepare 默认已经是预览 |

两套训练入口的参数不能互换：

| 入口 | --representation | --target | 数据划分 |
| --- | --- | --- | --- |
| `attribution train`（推荐） | stat98 / event / semantic | agent / llm | 先 split，再复用同一文件 |
| `train`（逻辑回归） | statistics / semantic / both | agent / llm / both | 训练时自动交叉验证 |

前者的 semantic 是动作序列，后者是 162 维动作统计；前者用 `--semantic-mode content/timing` 选择是否加入时间。

## 查完整参数

```bash
python -m agent_fingerprint --help
python -m agent_fingerprint collect --help
python -m agent_fingerprint attribution train --help
```

其他命令同样在末尾加 `--help`。`--config` 的含义随命令变化：collect 读取框架环境配置，experiment 读取实验矩阵，train 读取逻辑回归配置；attribution 的训练参数直接在命令行设置。
