#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model deepseek-chat \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id skyvern_deepseek_chat_shop \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
