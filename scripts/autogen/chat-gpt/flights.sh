#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent autogen \
  --model chat-gpt \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id autogen_chat_gpt_flights \
  --max-steps 50 \
  --autogen-screenshots \
  --autogen-trace \
  --no-network-probe \
  --timeout "${AUTOGEN_FLIGHTS_TIMEOUT:-3600}"
