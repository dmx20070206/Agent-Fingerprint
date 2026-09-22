#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model gemini \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id webvoyager_gemini_shop \
  --no-network-probe \
  --timeout 900

