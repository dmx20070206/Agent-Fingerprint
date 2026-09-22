#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model deepseek-chat \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id webvoyager_deepseek_shop \
  --no-network-probe \
  --timeout 900
