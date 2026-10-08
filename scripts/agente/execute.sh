#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
source "$script_dir/service.sh"
endpoint=${AGENTE_ENDPOINT:-http://127.0.0.1:8080/execute_task}
export AGENTE_ENDPOINT="$endpoint"
trap 'cleanup_agente_service $?' EXIT
ensure_agente_service "$endpoint" "$repo_root"
"$@"
