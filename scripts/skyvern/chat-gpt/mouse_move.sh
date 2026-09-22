#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model chat-gpt \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_move.jsonl \
  --run-id skyvern_chat_gpt_mouse_move \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
