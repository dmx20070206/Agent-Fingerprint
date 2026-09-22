#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model claude \
  --sandbox-directory sandbox/static \
  --task-file tasks/wheel_scroll.jsonl \
  --run-id browseruse_claude_wheel_scroll \
  --no-network-probe \
  --timeout 900

