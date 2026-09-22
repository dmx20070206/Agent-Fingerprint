#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model claude \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_click.jsonl \
  --run-id skyvern_claude_mouse_click \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
