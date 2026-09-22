#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model gemini \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id skyvern_gemini_forums \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
