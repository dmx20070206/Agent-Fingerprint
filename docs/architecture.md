# 项目架构

[文档导航](README.md)

主进程负责启动任务、采集数据和归档；各 Agent 在自己的 Conda 环境中运行。实现统一位于 `src/agent_fingerprint/`。

日常操作使用[命令速查](commands.md)；本页用于找代码和配置。核心边界是：采集保存原始运行，数据集把每次运行变成样本，训练使用样本预测框架或模型标签。

```text
collect / experiment
  → collection：启动网页服务、模型网关、探针
  → adapters → 各框架子进程 / 服务
  → storage：保存 manifest、原始事件和日志

原始 L3 事件 ┬→ features：98 维 v5 统计
            └→ semantic_actions：高层动作及内容/时序序列
  → dataset：一条完整 run 对应一个样本
  ├→ modeling：XGBoost / Transformer 四种表示实验
  └→ analysis：原有逻辑回归基线
```

## 找代码

| 模块 | 职责 |
| --- | --- |
| `cli/` | collect、experiment、extract 等入口和参数 |
| `collection/runner.py` | 单次运行、任务矩阵、资源启动与清理 |
| `collection/factories.py` | 创建网关、Agent 和抓包器 |
| `collection/gateway/`、`collection/probes/` | 模型调用、浏览器与网络采集 |
| `adapters/`、`runners/` | 主进程适配接口、框架环境中的执行代码 |
| `storage/` | 运行身份、文件归档、索引、整理与迁移 |
| `events/` | 共享数值解析、pointer/mouse 配对 |
| `features/` | 固定 98 维统计契约与计算 |
| `semantic_actions/` | 事件规范化、动作识别、合并与时间标注 |
| `analysis/` | 数据集、逻辑回归、交叉验证、预测与绘图 |
| `modeling/` | 四种表示的数据校验、随机划分、训练、评估与多 seed 汇总 |

统计与语义解析是并列分支；语义层不依赖统计模块的私有函数。同一份原始事件的 v5 计算口径固定。

## 找配置

| 文件 | 配置内容 |
| --- | --- |
| `configs/config.yaml` | 框架 Conda 环境名 |
| `configs/litellm.config.yaml` | 模型别名、上游路由和密钥变量 |
| `configs/experiments/default.yaml` | 实验矩阵及各组合采集参数 |
| `configs/classification.yaml` | 逻辑回归基线参数；不控制 attribution |
| `third_party/lock.json` | 外部框架 commit 与补丁校验和 |

实验参数按 `defaults → task → agent → model → overrides` 合并。配置支持 `${NAME:-default}` 环境变量默认值，解析时不会执行 Shell 表达式。

```bash
# 检查展开后的配置，不运行 Agent
python -m agent_fingerprint experiment \
  --agents browseruse --models claude --tasks flights --repeats 1 --dry-run

# 检查统一入口
python -m agent_fingerprint --help
```

`experiment` 实际执行时会保存配置快照、完整计划和每条命令的返回码。
增强语义探针通过版本化的 `AF_MONITOR_EXTENSION_API:1` 接口接入，使用 `collect --collector semantic/v1` 启用；版本及校验和写入 manifest。

目录移动保留 manifest 的 run ID；旧数据迁移需显式调用命令。具体见[数据契约](data-contract.md)，框架恢复见[第三方说明](../third_party/README.md)。
