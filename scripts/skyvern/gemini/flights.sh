#!/usr/bin/env bash
set -euo pipefail

# Skyvern's v2 planner consumes a JSON action object.
export SKYVERN_LLM_PROTOCOL=json

python orchestrator.py \
  --agent skyvern \
  --model gemini \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id skyvern_gemini_flights \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
