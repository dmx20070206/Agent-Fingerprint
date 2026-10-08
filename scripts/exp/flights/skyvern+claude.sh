#!/usr/bin/env bash
# Compatibility wrapper for repeated collection.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
if [[ $# -gt 1 ]]; then echo "Usage: $0 [positive run count]" >&2; exit 2; fi
exec bash "$repo_root/scripts/run_experiment.sh" --agents skyvern --models claude --tasks flights --repeats "${1:-5}"
