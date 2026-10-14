#!/usr/bin/env bash
set -euo pipefail

audit_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$audit_repo"
audit_conda="${CONDA_EXE:-$(command -v conda || true)}"
# cron 通常没有初始化 Conda，兼容本机的 Anaconda 安装位置。
if [[ -z "$audit_conda" && -x "$HOME/anaconda3/bin/conda" ]]; then
  audit_conda="$HOME/anaconda3/bin/conda"
fi
if [[ ! -x "$audit_conda" ]]; then
  echo '找不到 conda，请通过 CONDA_EXE 指定其可执行文件。' >&2
  exit 1
fi
exec "$audit_conda" run --no-capture-output -n agent-fingerprint \
  python scripts/dataset_audit.py --input-dir data/runs/final "$@"
