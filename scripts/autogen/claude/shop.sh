#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model claude \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id autogen_claude_shop \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900

