#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model gemini \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id skyvern_gemini_flights \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
