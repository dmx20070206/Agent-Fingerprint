#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model claude \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id skyvern_claude_forums \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
