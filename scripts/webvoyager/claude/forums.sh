#!/usr/bin/env bash
set -euo pipefail

python orchestrator.py \
  --agent webvoyager \
  --model claude \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id webvoyager_claude_forums \
  --no-network-probe \
  --timeout 900

