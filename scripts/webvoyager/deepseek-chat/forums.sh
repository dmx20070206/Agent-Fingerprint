#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model deepseek-chat \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id webvoyager_deepseek_forums \
  --no-network-probe \
  --timeout 900
