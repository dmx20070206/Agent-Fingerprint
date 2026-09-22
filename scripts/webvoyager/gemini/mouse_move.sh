#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model gemini \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_move.jsonl \
  --run-id webvoyager_gemini_mouse_move \
  --no-network-probe \
  --timeout 900

