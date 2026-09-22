#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model claude \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id webvoyager_claude_flights \
  --no-network-probe \
  --timeout 900

