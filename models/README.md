# 本地模型权重

`af vllm download <模型别名>` 将完整 Hugging Face 快照下载到本目录下对应子目录。
也可以手动放入已有权重；目录名见 `af vllm list`。权重不纳入 Git。

保留 tokenizer、processor、chat template 和模型配置文件；Phi-4 还需要
`vision-lora/`。不要只复制 safetensors 文件。

启动与任务调用见 [vLLM 使用指南](../docs/reference/local-models.md)。
