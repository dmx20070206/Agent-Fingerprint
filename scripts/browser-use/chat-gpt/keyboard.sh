#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model chat-gpt \
  --sandbox-directory sandbox/static \
  --task-file tasks/keyboard_type.jsonl \
  --run-id browseruse_chat_gpt_keyboard \
  --no-network-probe \
  --timeout 900
