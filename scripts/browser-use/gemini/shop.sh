#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model gemini \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id browseruse_gemini_shop \
  --no-network-probe \
  --timeout 900

