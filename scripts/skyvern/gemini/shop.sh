#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent skyvern \
  --model gemini \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id skyvern_gemini_shop \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
