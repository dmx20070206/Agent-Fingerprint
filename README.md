# Agent-Fingerprint

记录浏览器 Agent 如何点击、输入、滚动和调用模型，再用这些行为识别 **Agent 框架**或**底层 LLM**。
支持 Browser-use、WebVoyager、Skyvern、AutoGen、Agent-E，也支持人工操作和本地 Mock。

第一次使用，只需跑通本页末尾的 Mock 示例。已有采集数据，则直接从 `dataset` 开始；正式实验推荐使用 `attribution` 训练入口。

```text
采集 collect / experiment
  → 原始运行 data/runs（事件、标签、日志）
  → 构建数据集 dataset（每次完整运行对应一个样本）
  → 训练 attribution（预测 Agent 或 LLM）
  → 评估与汇总（指标、混淆矩阵、多随机种子对比）
```

## 从哪里开始

| 你想做什么 | 阅读位置 |
| --- | --- |
| 先跑通项目 | 本页末尾的运行示例 |
| 安装环境、配置真实模型 | [安装说明](docs/setup.md) |
| 采集单次任务或批量实验 | [采集指南与参数](docs/collection.md) |
| 从原始数据生成训练样本 | [数据说明](docs/data-contract.md) |
| 对比统计特征、事件序列、语义动作 | [训练指南与参数](docs/attribution-training.md) |
| 理解代码、特征或历史结果 | [完整文档导航](docs/README.md) |
| 忘记命令或混淆参数 | [命令与参数速查](docs/commands.md) |

## 安装

以下命令都在仓库根目录执行，要求 Python 3.11+。

```bash
# 最小环境：跑通本页示例，不需要 Conda 或 API key
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m agent_fingerprint --help
```

真实采集和模型训练需要额外依赖，见[安装说明](docs/setup.md)；其中也提供完整 Conda 环境安装方式。安装后 `af` 与 `python -m agent_fingerprint` 等价。

## 常用入口

| 命令 | 作用 | 常用参数 |
| --- | --- | --- |
| `collect` | 执行一次任务或任务文件 | `--agent` 框架；`--model` 模型别名；`--task-file` 任务文件 |
| `experiment` | 按框架 × 模型 × 任务批量采集 | `--agents/--models/--tasks` 逗号分隔；`--repeats` 重复次数；`--dry-run` 预览 |
| `extract` | 单份原始 JSON → 98 维统计特征和语义动作 | `--input-file` 输入；`--output-file` 输出；`--debug-output` 解析记录 |
| `dataset` | 运行目录 → `samples.json` | `--input-dir` 运行根目录；`--output-dir` 新数据集目录 |
| `attribution` | 训练、评估四种行为表示 | `split` 划分；`train` 训练；`evaluate` 重载评估；`aggregate` 汇总 |
| `prepare` | 整理运行目录、同步 ID 和索引 | 默认预览，`--apply` 执行 |

`extract` 便于查看单份轨迹；批量训练直接运行 `dataset`，它已经包含特征和语义提取。目录整理也是可选步骤。

完整参数在命令后加 `--help` 查看，例如 `python -m agent_fingerprint attribution train --help`。

训练有两个入口，参数和模型不能混用：

| 入口 | 模型与输入 | 用途 |
| --- | --- | --- |
| `attribution train` | 98 维统计 → XGBoost；原始事件 / 语义动作 → Transformer | 当前四种表示对比，详见[训练指南](docs/attribution-training.md) |
| `train` / `test` | 98 维统计或 162 维语义统计 → 逻辑回归 | [原有分类基线](docs/classification.md) |

## 目录速览

| 路径 | 内容 |
| --- | --- |
| `src/agent_fingerprint/` | 项目实现：采集、特征、语义解析、训练 |
| `configs/` | 框架环境、模型路由、实验矩阵、基线参数 |
| `tasks/`、`sandbox/` | 任务文件与本地测试网页 |
| `third_party/` | 外部框架源码、固定版本及补丁 |
| `tests/`、`examples/` | 测试与示例输入 |
| `data/` | 采集数据、数据集、模型和报告，不纳入 Git |
| `docs/` | 使用指南、技术参考、历史报告 |

## 完整运行示例：本地采集 → 提取 → 数据集

完成最小安装后，在 Bash 中连续执行。此示例不需要 API key、真实浏览器、第三方框架或 tcpdump。
每次创建新目录，便于重复运行。命令需在同一个 Bash 终端中连续执行，`DEMO_DIR` 是本次结果目录。

```bash
set -e
mkdir -p data/results
DEMO_DIR=$(mktemp -d data/results/demo.XXXXXX)

# 1. Mock Agent 访问本地页面、上传模拟点击、调用本地 Mock 模型
python -m agent_fingerprint collect \
  --agent mock --model mock-model --mock-llm --no-network-probe \
  --url /01-minimal.html --prompt '演示采集流程' \
  --run-id demo --task-id demo-task --site demo \
  --output-dir "$DEMO_DIR/run"

# 2. 提取这次运行的统计特征与语义动作
python -m agent_fingerprint extract \
  --input-file "$DEMO_DIR/run/fingerprints/l3_browser_dynamic.json" \
  --output-file "$DEMO_DIR/semantic.json" \
  --debug-output "$DEMO_DIR/semantic-debug.txt"

# 3. 构建数据集；输出质量报告，included 应为 1
python -m agent_fingerprint dataset \
  --input-dir "$DEMO_DIR/run" --output-dir "$DEMO_DIR/dataset"

# 4. 用仓库自带的事件示例查看动作抽象
python -m agent_fingerprint extract \
  --input-file examples/semantic_actions/example_raw.json \
  --output-file "$DEMO_DIR/example-semantic.json"

printf '结果目录：%s\n' "$DEMO_DIR"
```

| 示例参数 | 含义 |
| --- | --- |
| `--agent mock` / `--mock-llm` | 分别模拟 Agent 和模型服务，两项一起使用 |
| `--no-network-probe` | 跳过网络抓包，无需 tcpdump |
| `--url` / `--prompt` | 本地网页路径和任务要求 |
| `--output-dir` | 本次命令的输出目录 |
| `--run-id` / `--task-id` | 本次运行 ID / 任务 ID；同一任务可运行多次 |

预期结果：采集返回 `"status": "success"`；Mock 轨迹提取出 98 维特征；最后一步输出 `22 events -> 4 semantic actions; 98 v5 features`，动作依次为输入、提交、滚动、点击。

在打印的结果目录中查看：

| 文件 | 验证什么 |
| --- | --- |
| `run/manifest.json` | `status` 为 `success` |
| `semantic.json` | `existing_statistics.feature_count` 为 98 |
| `dataset/quality.json` | `included` 为 1，`excluded` 为空数组 |
| `dataset/samples.json` | 保存了本次运行的一个训练样本 |
| `example-semantic.json` | `action_sequence_content` 中有四个动作 |

这是流程验证；单条 Mock 样本不用于分类训练。正式训练请采集多个框架和模型的真实运行，再按[训练指南](docs/attribution-training.md)执行。
