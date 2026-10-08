# 安装与环境配置

[文档导航](README.md) · 下一步：[采集](collection.md)

主项目要求 **Python 3.11+**。以下命令均在仓库根目录执行。

| 你要做什么 | 安装到哪一步 |
| --- | --- |
| 跑本地示例、提取特征、构建数据集 | 最小主环境即可 |
| 用已有数据训练 | 主环境加 analysis、training 依赖 |
| 采集真实 Agent | 完整主环境 + 模型配置 + 对应框架环境 |

## 安装主环境

首次体验可创建最小环境，再运行[首页示例](../README.md)：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m agent_fingerprint --help
```

已有 Python 环境可跳过前两步。需要完整主环境时，也可选择 Conda：

```bash
conda env create -f environment.yml
conda activate agent-fingerprint
python -m agent_fingerprint --help
```

已有同名环境时更新：

```bash
conda env update -n agent-fingerprint -f environment.yml
conda activate agent-fingerprint
```

也可在已有 Python 3.11+ 环境中按需安装：

```bash
# 只提取特征、生成数据集、运行 Mock 示例
python -m pip install -e .

# 已有数据，只训练和分析
python -m pip install -e '.[analysis,training]'

# 完整功能
python -m pip install -e '.[collection,analysis,training,dev]'
```

| 依赖组 | 用途 |
| --- | --- |
| 基础依赖 | YAML、环境变量；Mock 采集、特征和语义解析、数据集构建 |
| `collection` | LiteLLM 网关，供真实模型调用 |
| `analysis` | 逻辑回归、模型保存、绘图 |
| `training` | `attribution` 使用的 XGBoost 和 PyTorch |
| `dev` | pytest、Ruff 等开发工具 |

`-e` 表示直接使用仓库源码；`.[analysis,training]` 中的方括号选择额外依赖组。项目使用 `src/` 布局，仅进入目录不足以导入包。

## 配置真实模型

本地 Mock 示例可以跳过本节。

```bash
cp -n .env.example .env
```

在 `.env` 填入实际密钥，再检查以下配置：

| 配置 | 需要检查什么 |
| --- | --- |
| `.env` | `LITELLM_MASTER_KEY` 及路由引用的上游密钥；主密钥需以 `sk-` 开头 |
| `configs/litellm.config.yaml` | `chat-gpt`、`claude` 等别名对应的供应商模型和密钥变量 |
| `configs/config.yaml` | 各 Agent 对应的 Conda 环境名 |
| `configs/experiments/default.yaml` | 批量实验的框架、模型、任务和重复次数 |

采集入口自动读取仓库 `.env`，已有环境变量优先。`--model` 是路由别名，具体上游版本取决于路由配置。

## 配置第三方框架

先恢复固定版本源码：

```bash
python scripts/setup_third_party.py --dry-run
python scripts/setup_third_party.py
```

脚本仅恢复源码和补丁，保留已有 checkout。它不会创建框架环境或安装依赖，详细规则见[第三方说明](../third_party/README.md)。

| 框架 | CLI 名称 | 默认 Conda 环境 | 安装依据 |
| --- | --- | --- | --- |
| Browser-use | `browseruse` | `browser-use` | 当前适配器面向 Browser-use 0.13.x |
| WebVoyager | `webvoyager` | `webvoyager` | `third_party/WebVoyager/requirements.txt` |
| Skyvern | `skyvern` | `skyvern` | `third_party/skyvern/pyproject.toml` 与 `uv.lock` |
| AutoGen | `autogen` | `autogen` | 当前适配器使用 AutoGen 0.4–0.7 API，依赖 `autogen-agentchat`、`autogen-ext` |
| Agent-E | `agente` | `agent-e` | `third_party/Agent-E/requirements.txt` |

框架依赖、浏览器和系统依赖装在各自环境，主环境负责调度。Agent-E 批量实验通过 `scripts/agente/execute.sh` 和 `service.sh` 管理服务。网络抓包另需可用的 tcpdump；只采集浏览器行为时可加 `--no-network-probe`。

## 检查与排错

```bash
python --version
python -m agent_fingerprint --help
python -m agent_fingerprint experiment \
  --agents browseruse --models claude --tasks flights --repeats 1 --dry-run

# 已安装 dev 依赖时运行；默认测试不调用付费模型
python -m pytest -q
```

| 现象 | 处理 |
| --- | --- |
| `No module named agent_fingerprint` | 激活正确环境，在仓库根目录执行 `python -m pip install -e .` |
| 缺少 `torch` / `xgboost` | 执行 `python -m pip install -e '.[analysis,training]'` |
| LiteLLM 提示没有主密钥 | 检查 `.env` 的 `LITELLM_MASTER_KEY`；离线验证可使用 `--agent mock --mock-llm` |
| 找不到框架或浏览器 | 检查框架自己的 Conda 环境及浏览器安装 |
| 输出目录已存在 | 换一个新目录；数据集和模型默认拒绝覆盖非空结果 |

Node 用于探针事件桩测试；真实浏览器和 LiteLLM 可选测试可能因缺少依赖而跳过。跳过测试不代表完成了真实框架验证。
未激活环境时，可用 `conda run --no-capture-output -n agent-fingerprint python -m agent_fingerprint ...` 显式指定主环境。
