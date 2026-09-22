#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model gemini \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id webvoyager_gemini_flights \
  --no-network-probe \
  --timeout 900

