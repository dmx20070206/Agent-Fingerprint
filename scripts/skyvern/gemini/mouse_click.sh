#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model gemini \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_click.jsonl \
  --run-id skyvern_gemini_mouse_click \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
