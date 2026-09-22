#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model chat-gpt \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_click.jsonl \
  --run-id webvoyager_chat_gpt_mouse_click \
  --no-network-probe \
  --timeout 900
