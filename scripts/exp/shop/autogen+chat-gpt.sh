#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../../.." && pwd)
cd -- "$repo_root"

runs=${1:-5}
if [[ $# -gt 1 || ! "$runs" =~ ^[1-9][0-9]*$ ]]; then
  echo "Usage: $0 [positive run count, default: 5]" >&2
  exit 2
fi

failures=0
for ((i = 1; i <= runs; i++)); do
  echo "[autogen + chat-gpt / shop] Run $i/$runs"
  if bash "scripts/autogen/chat-gpt/shop.sh"; then
    echo "Run $i completed"
  else
    status=$?
    echo "Run $i failed (exit $status)" >&2
    failures=$((failures + 1))
  fi
done

echo "[autogen + chat-gpt / shop] Finished $runs runs; failures: $failures"
[[ "$failures" -eq 0 ]]
