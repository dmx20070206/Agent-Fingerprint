#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model deepseek-chat \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id autogen_deepseek_chat_shop \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
