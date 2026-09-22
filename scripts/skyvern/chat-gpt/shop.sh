#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model chat-gpt \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id skyvern_chat_gpt_shop \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
