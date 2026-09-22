#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model deepseek-chat \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id autogen_deepseek_chat_flights \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
