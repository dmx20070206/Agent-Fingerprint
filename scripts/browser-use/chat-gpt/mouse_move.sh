#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model chat-gpt \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_move.jsonl \
  --run-id browseruse_chat_gpt_mouse_move \
  --no-network-probe \
  --timeout 900
