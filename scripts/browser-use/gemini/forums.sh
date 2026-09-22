#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model gemini \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id browseruse_gemini_forums \
  --no-network-probe \
  --timeout 900

