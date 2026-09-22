#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model deepseek-chat \
  --sandbox-directory sandbox/static \
  --task-file tasks/keyboard_type.jsonl \
  --run-id autogen_deepseek_chat_keyboard \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
