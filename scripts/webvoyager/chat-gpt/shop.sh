#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model chat-gpt \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id webvoyager_chat_gpt_shop \
  --no-network-probe \
  --timeout 900
