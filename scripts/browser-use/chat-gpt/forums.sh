#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model chat-gpt \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id browseruse_chat_gpt_forums \
  --no-network-probe \
  --timeout 900
