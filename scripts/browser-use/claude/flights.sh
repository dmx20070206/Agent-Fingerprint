#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model claude \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id browseruse_claude_flights \
  --no-network-probe \
  --timeout 900

