#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../../.." && pwd)
source "$repo_root/scripts/agente/service.sh"

endpoint=${AGENTE_ENDPOINT:-http://127.0.0.1:8080/execute_task}
trap 'cleanup_agente_service $?' EXIT
ensure_agente_service "$endpoint" "$repo_root"

cd -- "$repo_root"

python orchestrator.py \
  --agent agente \
  --agente-endpoint "$endpoint" \
  --model deepseek-chat \
  --sandbox-directory sandbox/shop \
  --task-file tasks/shop.jsonl \
  --run-id agente_deepseek_chat_shop \
  --max-steps 50 \
  --no-network-probe \
  --timeout 900
