#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model gemini \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id browseruse_gemini_flights \
  --no-network-probe \
  --timeout 900

