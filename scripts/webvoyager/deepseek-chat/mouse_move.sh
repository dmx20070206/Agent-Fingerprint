#!/usr/bin/env bash
# Compatibility wrapper; experiment settings live in configs/experiments/default.yaml.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
exec bash "$repo_root/scripts/run_experiment.sh" --agents webvoyager --models deepseek-chat --tasks mouse_move --repeats 1 "$@"
