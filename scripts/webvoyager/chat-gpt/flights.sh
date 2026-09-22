#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model chat-gpt \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id webvoyager_chat_gpt_flights \
  --no-network-probe \
  --timeout 900
