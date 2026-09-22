#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model claude \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_move.jsonl \
  --run-id webvoyager_claude_mouse_move \
  --no-network-probe \
  --timeout 900

