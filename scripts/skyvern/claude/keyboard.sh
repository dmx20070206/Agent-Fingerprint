#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model claude \
  --sandbox-directory sandbox/static \
  --task-file tasks/keyboard_type.jsonl \
  --run-id skyvern_claude_keyboard \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
