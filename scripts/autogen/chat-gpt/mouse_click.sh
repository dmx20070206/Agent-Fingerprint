#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model chat-gpt \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_click.jsonl \
  --run-id autogen_chat_gpt_mouse_click \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
