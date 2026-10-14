# 快速开始

[文档索引](README.md) · 下一步：[日常工作流](workflow.md)

本页用 Mock 验证“采集 → 数据集”能否跑通，不需要模型密钥、真实浏览器或 GPU。所有命令都在仓库根目录执行。

## 使用主环境

项目使用 Python 3.11+，统一在 Conda `agent-fingerprint` 环境运行：

```bash
conda activate agent-fingerprint
python -m agent_fingerprint --help
```

已有环境无需重建。仅在新机器尚未安装时运行 `conda env create -f environment.yml`；需要同步依赖时运行 `conda env update -n agent-fingerprint -f environment.yml`，再激活环境。该文件安装完整主环境，不另建 Python venv。

无法激活 Shell 环境时，也可显式执行：

```bash
conda run --no-capture-output -n agent-fingerprint python -m agent_fingerprint --help
```

## 验证本地流程

在同一个 Bash 终端执行，每次生成独立结果目录：

```bash
mkdir -p data/results
DEMO_DIR=$(mktemp -d data/results/demo.XXXXXX)

python -m agent_fingerprint collect \
  --agent mock --model mock-model --mock-llm --no-network-probe \
  --url /01-minimal.html --prompt '演示采集流程' \
  --run-id demo --task-id demo-task --site demo \
  --output-dir "$DEMO_DIR/run"

python -m agent_fingerprint dataset \
  --input-dir "$DEMO_DIR/run" --output-dir "$DEMO_DIR/dataset"

printf '结果目录：%s\n' "$DEMO_DIR"
```

检查 `run/manifest.json` 的 `status` 为 `success`，以及 `dataset/quality.json` 的 `included` 为 1。原始事件在 `run/fingerprints/l3_browser_dynamic.json`，生成的样本在 `dataset/samples.json`。

这只验证数据流程。分类训练需要多个类别、足够数量的真实运行，单条 Mock 样本不用于训练。

## 使用真实 Agent 前

1. 将 `.env.example` 复制为 `.env`，填写模型密钥；已有 `.env` 直接编辑。`configs/litellm.config.yaml` 定义 `claude` 等模型别名与实际路由。
2. 按[第三方框架说明](../third_party/README.md)准备框架源码和各自的 Conda 环境，检查 `configs/config.yaml` 中的环境名。主环境负责调度，框架依赖装在各自环境。
3. 使用本地权重时，先按[本地模型参考](reference/local-models.md)启动 vLLM；云端模型可跳过。

然后进入[日常工作流](workflow.md)。配置文件职责见[架构与配置](reference/architecture.md#找配置)。

## 常见环境问题

| 现象 | 处理 |
| --- | --- |
| `No module named agent_fingerprint` | 激活主环境，在仓库根目录执行 `python -m pip install -e .` |
| 缺少 `numpy` / `torch` / `xgboost` | 在主环境执行 `python -m pip install -e '.[analysis,training]'` |
| 模型鉴权失败 | 检查 `.env`、网关路由及 `LITELLM_MASTER_KEY`（以 `sk-` 开头）；已有环境变量优先 |
| 找不到框架或浏览器 | 检查对应框架环境及浏览器安装 |
| 输出目录已存在 | 换一个新结果目录，数据集和训练默认拒绝覆盖非空目录 |
