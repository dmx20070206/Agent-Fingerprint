#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model chat-gpt \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id skyvern_chat_gpt_flights \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
