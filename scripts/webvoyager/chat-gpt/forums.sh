#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model chat-gpt \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id webvoyager_chat_gpt_forums \
  --no-network-probe \
  --timeout 900
