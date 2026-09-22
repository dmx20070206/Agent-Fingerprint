#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model chat-gpt \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id autogen_chat_gpt_shop \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
