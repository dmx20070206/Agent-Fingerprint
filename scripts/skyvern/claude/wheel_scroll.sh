#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model claude \
  --sandbox-directory sandbox/static \
  --task-file tasks/wheel_scroll.jsonl \
  --run-id skyvern_claude_wheel_scroll \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
