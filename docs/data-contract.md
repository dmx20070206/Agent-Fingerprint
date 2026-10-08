# 数据：目录、数据集与运行身份

[文档导航](README.md) · 上一步：[采集](collection.md) · 下一步：[训练](attribution-training.md)

数据分三层：**原始运行 → 派生数据集 → 训练与评估产物**。`extract` 处理一份原始 JSON；`dataset` 扫描一批运行并生成训练样本，内部已包含统计和语义提取，无需先逐份运行 extract。

训练前只需完成“构建数据集”并检查 `quality.json`。后面的身份修复和迁移用于维护旧数据，正常新采集可跳过。

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

## 构建数据集

```bash
python -m agent_fingerprint dataset \
  --input-dir data/runs --output-dir data/datasets/my_experiment

# 只选择部分网页、框架和模型；改用新输出目录
python -m agent_fingerprint dataset \
  --input-dir data/runs --output-dir data/datasets/flights_subset \
  --sites flights --agents browseruse,skyvern --llms chat-gpt,deepseek-chat
```

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
| `prepare --legacy-short-ids` | 显式启用旧式短编号，记录 `run_names.json`；通常不需要 |
| `normalize --from-directory` | 旧式修复：明确以目录名作为身份；通常使用 manifest 身份即可 |

`organize --input-dir data/runs` 可单独预览目录归类，加 `--apply` 执行。通常使用 `prepare` 即可，它统一处理目录、ID 与索引。

整理默认保留 manifest ID，`run_moves.json` 记录位置变化；若 L3 或结构化日志 ID 不一致，会同步该字段。不会重排原始事件、改写 session/document ID 或二进制文件。
v1 迁移先复制构建 v2，并保留 `artifacts/legacy-v1` 原始备份，完成新目录和索引后才移除旧位置；已有 v2 不重复迁移，目标冲突会拒绝执行。
目录或身份变化后，用新输出路径重建数据集，避免旧 `source_path` 与哈希失效。

## 复现时保留什么

保留原始运行、数据集、训练配置和 split manifest。语义 JSON 自带解析器版本、阈值、原始事件及规则审计；模型仅使用明确的输入字段，不把 URL、按钮文字、审计字段和标签混入特征。
比较实验时检查 collector 版本：增强探针会改变新数据的观测内容，但同一份 E 的 v5 计算保持不变。
