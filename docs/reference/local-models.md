# 用 vLLM 调用本地开源模型

调用链与现有模型一致：`Agent → LiteLLM（API key）→ vLLM（API key）→ models/ 中的权重`。
采集时只需更换 `--model`，输出数据中的模型标签也使用该别名。
vLLM 需提前启动；`collect` 自动管理 LiteLLM，不负责加载或卸载 GPU 权重。

## 模型与目录

| `--model` 别名 | Hugging Face 仓库 / `models/` 子目录 | 端口 | 默认 GPU 数 |
| --- | --- | --- | --- |
| `qwen3.5-9b` | `Qwen/Qwen3.5-9B` / `Qwen3.5-9B` | 8101 | 1 |
| `glm-4.6v-flash` | `zai-org/GLM-4.6V-Flash` / `GLM-4.6V-Flash` | 8102 | 1 |
| `qwen3.5-27b` | `Qwen/Qwen3.5-27B` / `Qwen3.5-27B` | 8103 | 4 |
| `gemma-4-12b` | `google/gemma-4-12B-it` / `gemma-4-12B-it` | 8104 | 2 |
| `ministral-3-8b` | `mistralai/Ministral-3-8B-Instruct-2512` / `Ministral-3-8B-Instruct-2512` | 8105 | 1 |
| `phi-4-multimodal` | `microsoft/Phi-4-multimodal-instruct` / `Phi-4-multimodal-instruct` | 8106 | 1 |

GPU 数是面向 24GB 卡的起始配置，**尚未经这六个模型的真实推理验证**。能否加载还取决于空闲显存、图像分辨率、上下文和 vLLM 版本。
默认上下文 8192、并发 1；浏览器历史较长时需要提高上下文，并相应增加显存。
默认逐模型运行，结束后 Ctrl-C 释放显存，再启动下一个。不同模型可在不同 GPU 上并行服务，但要自行分配显存。

模型路径和启动参数在 [vllm.models.yaml](../../configs/vllm.models.yaml)，网关路由在
[litellm.config.yaml](../../configs/litellm.config.yaml)。修改服务端口或部署到其他主机时，也要修改对应路由的 `api_base`。
相对权重路径基于项目根目录，因此可从其他工作目录调用 `af vllm`。

## 环境与密钥

以下命令在项目根目录运行。为 vLLM 创建独立的 Conda 环境，避免改动已有 Agent 环境的 Torch/Transformers：

```bash
conda create -n vllm python=3.11 -y
conda activate vllm
python -m pip install --upgrade pip
python -m pip install --upgrade vllm huggingface_hub
python -m pip install -e .
vllm --version
```

需要与机器 CUDA/驱动匹配、支持这些新架构的 vLLM；按
[官方安装说明](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/)
选择 wheel。Gemma 4 12B 需要支持 `Gemma4UnifiedForConditionalGeneration`，仅支持其他 Gemma 4 型号的旧版本不够。
Ministral 的 tokenizer 如提示缺依赖，在该环境安装 `mistral_common[image]`。
实验前记录实际 vLLM/Transformers 版本和权重 revision。

在项目已有 `.env` 中添加以下变量（不要覆盖已有供应商密钥）：

```dotenv
LITELLM_MASTER_KEY=sk-your-gateway-key
VLLM_API_KEY=sk-your-local-vllm-key
```

两者是自己设置的本地服务密钥。Agent 使用 `LITELLM_MASTER_KEY`，LiteLLM 访问 vLLM 使用 `VLLM_API_KEY`。
启动命令自动读取项目 `.env`；环境中已设置的值优先。密钥不写进 YAML 或启动命令参数。
若机器配置了 HTTP/HTTPS 代理，为采集进程设置 `NO_PROXY=localhost,127.0.0.1,::1`，
让 Agent 和 LiteLLM 直接访问本地服务；`af vllm check` 已自动绕过本地地址的代理。

## 下载、启动与检查

以下命令在 `vllm` Conda 环境中执行，启动程序会使用该环境中的 `vllm` 可执行文件。

```bash
conda activate vllm

# 查看别名和路径
af vllm list

# 下载一个模型；已有完整权重目录可跳过
af vllm download qwen3.5-9b
# 下载全部六个（较大）：将上一行别名换成 all
# 固定版本：af vllm download qwen3.5-9b --revision <commit-sha>

# 先预览；不会加载权重或占用 GPU
af vllm serve qwen3.5-9b --gpus 1 --dry-run

# 前台启动，等日志显示服务器就绪后再运行任务
af vllm serve qwen3.5-9b --gpus 1
```

下载使用完整官方仓库快照，包含 tokenizer、processor、模板和适配器。
若仓库要求登录或接受许可，先在同一环境运行 `hf auth login`，或设置 `HF_TOKEN`。
权重保存在项目 `models/`，不会纳入 Git。可通过配置的 `models_root` 改为已有权重目录。

另开终端，确认文本和截图输入均可返回结果：

```bash
conda activate vllm
af vllm check qwen3.5-9b
af vllm check qwen3.5-9b --image /absolute/path/screenshot.png
```

检查会使用 Bearer key 请求 `/v1/models` 和 `/v1/chat/completions`；只通过文本检查不代表已验证视觉能力。
推理模型若耗尽输出预算，可增大 `--max-tokens`。检查不验证框架所需的全部工具调用或 JSON schema 功能，正式批量实验前先跑一次目标框架任务。

其他模型的启动例子（按当时空闲 GPU 修改编号）：

```bash
conda activate vllm
af vllm serve qwen3.5-27b --gpus 1,2,3,4
af vllm serve gemma-4-12b --gpus 1,2
```

`--tensor-parallel-size`、`--max-model-len`、`--gpu-memory-utilization`、`--max-num-seqs` 可覆盖配置。
单卡 9B 显存不足时，可尝试 `--gpus 1,2 --tensor-parallel-size 2`；实际并行度须满足模型架构约束。
更多模型专用参数可编辑配置中的 `serve_args`。

## 运行原有采集任务

保持 vLLM 服务终端运行，另开终端切换到原有采集环境，保留现有框架安装和 LiteLLM 依赖。
以下使用项目 `environment.yml` 中的环境名；若已有环境使用其他名称，请相应替换：

```bash
conda activate agent-fingerprint
af collect --agent webvoyager --model qwen3.5-9b \
  --task-file tasks/shop.jsonl --sandbox-directory sandbox/shop \
  --site shop --no-network-probe

# 批量运行当前已启动的模型；其余模型按同样方式依次执行
af experiment --agents webvoyager --models qwen3.5-9b --tasks shop --repeats 5
```

六个别名均已注册到默认实验配置，可用 `--models` 逗号分隔选择多个；所选服务必须提前启动。
原默认矩阵仍运行已有云端模型。

直接使用 SDK 时，vLLM 的 `base_url` 是 `http://127.0.0.1:8101/v1`，`api_key` 是
`VLLM_API_KEY`，`model` 是 `qwen3.5-9b`。若通过正在运行的 LiteLLM，则使用其端口 4000 和
`LITELLM_MASTER_KEY`。`collect` 结束后会停止它创建的 LiteLLM，但外部 vLLM 继续运行。

## 各模型的差异与验证边界

- Qwen 使用 `qwen3` reasoning parser 和 `qwen3_coder` tool parser，见 [官方模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)。
- GLM 使用 `glm45` parsers，见 [vLLM GLM 配方](https://github.com/vllm-project/recipes/blob/main/GLM/GLM-V.md)。旧版本的视觉与工具调用组合可能有兼容问题。
- Gemma 使用指令版本 `12B-it`、仓库自带聊天模板及 `gemma4` parsers，见 [模型卡](https://huggingface.co/google/gemma-4-12B-it) 和 [vLLM 配方](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)。
- Ministral 使用官方 FP8 仓库及 Mistral tokenizer/config/load 格式，见 [模型卡](https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512)。它与其他 BF16 权重的精度差异需要记入实验设置。
- Phi 保留官方要求的 `trust-remote-code`，以 `--default-mm-loras` 自动为图片请求加载本地 `vision-lora`、rank 320。见 [模型卡](https://huggingface.co/microsoft/Phi-4-multimodal-instruct) 和 [vLLM 多模态 LoRA](https://docs.vllm.ai/en/stable/features/lora/)。当前配置聚焦文本和截图；不启用音频适配器。未配置原生工具调用 parser，优先用于 WebVoyager 这类文本动作协议；不要直接视为 AutoGen/Agent-E 原生工具调用的等价基线。

这里提供的是接入配置与启动工具，不代表六个模型均已完成浏览器实验。真实验证应分别检查鉴权、截图理解、目标框架的动作输出，再检查任务完成状态。
