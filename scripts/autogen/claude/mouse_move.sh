#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model claude \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_move.jsonl \
  --run-id autogen_claude_mouse_move \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900

