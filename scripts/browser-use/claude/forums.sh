#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent browseruse \
  --model claude \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id browseruse_claude_forums \
  --no-network-probe \
  --timeout 900

