#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model deepseek-chat \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id autogen_deepseek_chat_forums \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
