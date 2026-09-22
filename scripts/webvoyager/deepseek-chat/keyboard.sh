#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model deepseek-chat \
  --sandbox-directory sandbox/static \
  --task-file tasks/keyboard_type.jsonl \
  --run-id webvoyager_deepseek_keyboard \
  --no-network-probe \
  --timeout 900
