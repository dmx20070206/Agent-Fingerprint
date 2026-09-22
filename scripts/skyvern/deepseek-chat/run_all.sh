#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

for script in "$script_dir"/*.sh; do
    [[ -f "$script" ]] || continue
    [[ "${script##*/}" == "run_all.sh" ]] && continue

    printf 'Running: %s\n' "$script"
    bash "$script"
done
