#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model deepseek-chat \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_move.jsonl \
  --run-id webvoyager_deepseek_mouse_move \
  --no-network-probe \
  --timeout 900
