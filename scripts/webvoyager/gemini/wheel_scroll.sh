#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model gemini \
  --sandbox-directory sandbox/static \
  --task-file tasks/wheel_scroll.jsonl \
  --run-id webvoyager_gemini_wheel_scroll \
  --no-network-probe \
  --timeout 900

