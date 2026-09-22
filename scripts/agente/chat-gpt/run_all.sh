#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../../.." && pwd)
source "$repo_root/scripts/agente/service.sh"

endpoint=${AGENTE_ENDPOINT:-http://127.0.0.1:8080/execute_task}
trap 'cleanup_agente_service $?' EXIT
ensure_agente_service "$endpoint" "$repo_root"

cd -- "$repo_root"

for script in "$script_dir"/*.sh; do
    [[ -f "$script" ]] || continue
    [[ "${script##*/}" == "run_all.sh" ]] && continue

    printf 'Running: %s\n' "$script"
    bash "$script"
done
