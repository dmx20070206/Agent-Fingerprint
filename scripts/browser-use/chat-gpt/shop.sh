#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model chat-gpt \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id browseruse_chat_gpt_shop \
  --no-network-probe \
  --timeout 900
