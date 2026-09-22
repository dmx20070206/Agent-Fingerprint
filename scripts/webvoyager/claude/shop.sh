#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model claude \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id webvoyager_claude_shop \
  --no-network-probe \
  --timeout 900

