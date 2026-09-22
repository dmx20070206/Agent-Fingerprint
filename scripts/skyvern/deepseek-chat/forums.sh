#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model deepseek-chat \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id skyvern_deepseek_chat_forums \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
