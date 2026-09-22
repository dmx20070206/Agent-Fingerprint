#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model deepseek-chat \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_click.jsonl \
  --run-id autogen_deepseek_chat_mouse_click \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
