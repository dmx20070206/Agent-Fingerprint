# 数据：目录、数据集与运行身份

[文档导航](../README.md) · 上一步：[采集](collection.md) · 下一步：[训练](../workflow.md#训练与评估)

数据分三层：**原始运行 → 派生数据集 → 训练与评估产物**。`extract` 处理一份原始 JSON；`dataset` 扫描一批运行并生成训练样本，内部已包含统计和语义提取，无需先逐份运行 extract。

本页用于查询格式、标签及目录维护规则。日常处理按[工作流](../workflow.md)执行。

## 文件存在哪里

```text
data/
  runs/<日期>/<run_id>/
    manifest.json                 # 框架、模型、任务、状态、采集器信息
    fingerprints/
      l1_http_tls.json             # HTTP / TLS 与网络指纹
      l2_browser_static.json       # 浏览器静态属性
      l3_browser_dynamic.json      # 原始交互事件（训练的数据来源）
      l4_agent_trace.json          # Agent / LLM 调用轨迹
    artifacts/                    # PCAP、截图、录像、框架产物
    logs/                         # 执行日志
  datasets/<名称>/
    samples.json                  # 一条完整 run 对应一个样本
    quality.json                  # 纳入、排除、缺失值与类别分布
  experiments/<名称>/             # 训练配置、模型和评估
  results/<名称>/                 # 提取结果、汇总及审计报告
```

各层是否有实际内容取决于采集开关和框架能力。`--output-dir` 可以将单次运行保存到指定位置。

## 数据集参数与质量规则

构建命令见[工作流](../workflow.md#构建数据集)。

| 参数 | 说明 |
| --- | --- |
| `--input-dir` | 递归扫描的运行根目录；默认 `data/runs/final`，建议显式指定 |
| `--output-dir` | 必填，数据集输出目录，拒绝覆盖非空目录 |
| `--sites` | 网页标签，如 `flights,shop`；论坛数据使用 `forum` |
| `--agents` | 框架标签，如 `browseruse,skyvern` |
| `--llms` | 模型标签，如 `chat-gpt,deepseek-chat` |

筛选项用逗号分隔；省略或 `all` 表示全选。先查看 `quality.json`：`included` 为纳入数量，`excluded` 为质量排除及原因，`filtered_out` 为主动筛掉的运行。

| 检查项 | 如何处理 |
| --- | --- |
| `included` 为 0 | 检查输入目录、筛选标签和 excluded 中的原因 |
| 类别只剩一个或样本过少 | 增加其他框架 / 模型的运行，再训练对应分类目标 |
| 原始文件已移动或修改 | 使用新输出目录重新构建数据集 |

空事件、身份不一致、缺少标签、重复运行或重复事件轨迹会被排除。失败状态但轨迹可用的运行仍可纳入，并记录警告。

`samples.json` 同时保存 98 维 `features`、162 维 `semantic_features`、内容/时序动作序列，以及原始文件和 manifest 的 SHA-256。原始事件 E 仍从 `source_path` 读取，因此训练前不能删除或随意移动原始数据。

## run ID 与 task ID 的区别

| 字段 | 含义 |
| --- | --- |
| `manifest.run_id` | 一次完整运行的稳定身份；移动目录不会改变它 |
| `manifest.task.task_id` | 任务身份；同一个任务可以重复运行多次 |
| `manifest.task.site` | 网页集合标签 |
| `manifest.agent.name/model` | 框架与模型标签 |
| `manifest.collector.version/sha256` | 采集器版本与脚本校验和 |

`collect --task-id` 设置任务 ID；`extract --task-id` 是历史命名，实际用于查找运行的 **run ID**。为了避免混淆，单文件提取优先使用 `--input-file`。
新数据显式保存标签；旧数据缺失时才兼容目录推断。模型别名不等于固定的上游版本，复现实验需同时保留路由配置。

## 整理与修复（按需执行）

正常构建数据集不要求先整理目录。

```bash
# 默认只预览，按 site/agent/model/run_id 规划目录
python -m agent_fingerprint prepare --input-dir data/runs

# 执行整理，保存移动记录并更新索引
python -m agent_fingerprint prepare --input-dir data/runs \
  --apply --report data/results/prepare_report.json

# 只检查或修复结构化 ID，不移动目录
python -m agent_fingerprint normalize --input-dir data/runs
python -m agent_fingerprint normalize --input-dir data/runs \
  --apply --report data/results/normalize_report.json

# 旧 v1 归档迁移：预览 / 执行
python -m agent_fingerprint migrate --root data/runs
python -m agent_fingerprint migrate --root data/runs --apply
```

| 参数 | 说明 |
| --- | --- |
| `--apply` | 将预览方案写入数据；省略时不改运行文件 |
| `prepare --index` | 指定运行索引位置，默认位于输入根目录 |
| `prepare --short-ids` | 按任务/agent/模型分别从 1 编号为 `run_0001`，记录 `run_names.json`；兼容旧参数 `--legacy-short-ids` |
| `normalize --from-directory` | 旧式修复：明确以目录名作为身份；通常使用 manifest 身份即可 |

### 自动归类并统一为 run_x

把待整理的完整运行目录放入 `data/runs/final`，可以平铺，也可以保留日期子目录，然后运行：

```bash
# 预览：不移动或改写运行数据
python -m agent_fingerprint prepare --input-dir data/runs/final --short-ids

# 执行：移动完整运行目录，同步结构化 run_id 并更新索引
python -m agent_fingerprint prepare --input-dir data/runs/final --short-ids \
  --apply --report data/results/prepare_final.json
```

结果例如：

```text
data/runs/final/
├── flights/browseruse/qwen3.5-9b/run_0001/
└── shop/agente/qwen3.5-9b/run_0001/
```

`--input-dir` 可替换为任意待整理文件夹；它也是整理后的根目录，命令在其中移动数据，不复制到其他位置。只对有 `manifest.json` 的完整运行归类，保留运行内所有文件，不将内部框架 manifest 当成另一场实验。请在采集结束后整理。

任务目录优先使用 manifest 的 `task.site`（其次顶层 `site`）；旧数据可从已有分类路径、目录名后缀或任务 URL 推断。这里的“任务名”是 `flights`、`shop` 等网站/任务类别，不是 `task_id`。agent 和模型分别读取 `agent.name`、`agent.model`。无法识别或标签不安全时会报错，请补齐 manifest 的 `task.site` 后重试。

编号在每个 `任务/agent/模型` 文件夹内独立从 `run_0001` 开始；不同任务、agent 或模型可以有相同的短编号。执行时按原短编号数值顺序排列已有运行，再追加新采集运行，并统一生成连续编号，因此旧的全局编号也会改为组内编号，删除数据留下的编号空缺会收紧。`run_names.json` 保存原路径、原 ID 与新路径、新 ID 的映射。索引按完整路径区分运行，整理时重建本根目录的索引项以移除旧编号引用。成功和失败运行都会整理，不按实验结果筛选。

`run_id` 只是组内编号。数据集的 `sample_id`（相对于数据根目录的完整运行路径）用于样本和 attribution 会话身份，训练/测试的身份检查使用任务、agent、模型和短编号的组合；原始事件哈希去重仍然保留。整理后请重新生成数据集、划分与训练结果，旧结果中的路径和哈希不会自动改写。

查询单次运行时推荐提供完整相对路径，输出也保留该层级：

```bash
python -m agent_fingerprint extract --input-dir data/runs/final \
  --task-id shop/agente/qwen3.5-9b/run_0001 --output-dir data/results
```

裸 `--task-id run_0001` 仅在输入目录内唯一时可用；遇到多个匹配会报错。attribution 的 `--session-id` 使用数据集中的完整 `sample_id`。

`prepare --legacy-short-ids` 是 `prepare --short-ids` 的兼容写法。`normalize` 只修复文件内部的 ID，**不会将目录重命名为 run_x**。

`organize --input-dir data/runs` 可单独预览目录归类，加 `--apply` 执行。通常使用 `prepare` 即可，它统一处理目录、ID 与索引。

整理默认保留 manifest ID，`run_moves.json` 记录位置变化；若 L3 或结构化日志 ID 不一致，会同步该字段。不会重排原始事件、改写 session/document ID 或二进制文件。
v1 迁移先复制构建 v2，并保留 `artifacts/legacy-v1` 原始备份，完成新目录和索引后才移除旧位置；已有 v2 不重复迁移，目标冲突会拒绝执行。
目录或身份变化后，用新输出路径重建数据集，避免旧 `source_path` 与哈希失效。

## 复现时保留什么

保留原始运行、数据集、训练配置和 split manifest。语义解析 Python API 可返回解析器版本、阈值和规则审计；`extract` 导出的精简 JSON 仅含内容与时序序列。模型仅使用明确的输入字段，不把 URL、按钮文字、审计字段和标签混入特征。
比较实验时检查 collector 版本：增强探针会改变新数据的观测内容，但同一份 E 的 v5 计算保持不变。
