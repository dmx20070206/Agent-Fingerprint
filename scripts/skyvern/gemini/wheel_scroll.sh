#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model gemini \
  --sandbox-directory sandbox/static \
  --task-file tasks/wheel_scroll.jsonl \
  --run-id skyvern_gemini_wheel_scroll \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
