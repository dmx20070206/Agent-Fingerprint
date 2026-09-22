#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model gemini \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_click.jsonl \
  --run-id browseruse_gemini_mouse_click \
  --no-network-probe \
  --timeout 900

