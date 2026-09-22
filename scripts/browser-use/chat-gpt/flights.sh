#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model chat-gpt \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id browseruse_chat_gpt_flights \
  --no-network-probe \
  --timeout 900
