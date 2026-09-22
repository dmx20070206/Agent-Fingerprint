#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model claude \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id skyvern_claude_flights \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
