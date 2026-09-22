#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model gemini \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id webvoyager_gemini_forums \
  --no-network-probe \
  --timeout 900

