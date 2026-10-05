#!/usr/bin/env bash
# Reusable offline workflow. Relative paths are resolved from the repository root.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() {
  cat <<'HELP'
Usage: bash scripts/run_analysis.sh [all|prepare|dataset|train|test|predict|plot] [options]

  all       Prepare runs, build dataset, train both classifiers, run regression tests (default)
  prepare   Group runs, shorten folder names, normalize run_id and update index
  dataset   Build features and quality report
  train     Train and cross-validate agent/llm classifiers from an existing dataset
  test      Run offline analysis regression tests (not a held-out model evaluation)
  plot      Render PNG/SVG charts from saved evaluation results (no retraining)
  predict   Predict a single raw L3 file with an existing model

Options:
  --input-dir PATH          Run root (default: data/runs/final)
  --sites NAMES            Website collections, comma-separated (default: all)
  --agents NAMES           Agent labels, comma-separated (default: all)
  --llms NAMES             LLM labels, comma-separated (default: all)
  --run-name NAME           Output suffix (default: unique UTC timestamp + process ID)
  --dataset-dir PATH        Dataset output/input directory
  --agent-model-dir PATH    Agent model output/input directory
  --llm-model-dir PATH      LLM model output/input directory
  --config PATH            Training config (default: config/classification.yaml)
  --target agent|llm|both   Training/plot target (default: both)
  --index PATH             Index to update (default: INPUT_DIR/index.jsonl)
  --report PATH            Preparation audit JSON (default: data/results/prepare_NAME.json)
  --dry-run                Preview prepare only; do not modify runs
  --full                   With test: run the entire repository test suite
  --predict-input PATH     Raw L3 JSON (required for predict; optional after all/train)
  --predict-target TARGET  agent or llm (default: agent)
  -h, --help               Show this help

Set PYTHON to select a Python interpreter. Existing nonempty outputs are never overwritten.
For separate dataset/train/predict calls, reuse --run-name or explicit output directories.
HELP
}
fail() { echo "Error: $*" >&2; exit 2; }
COMMAND=all
if [[ $# -gt 0 && "$1" != -* ]]; then COMMAND="$1"; shift; fi
case "$COMMAND" in all|prepare|dataset|train|test|predict|plot) ;; *) fail "Unknown command: $COMMAND" ;; esac
PYTHON="${PYTHON:-python}"
INPUT_DIR=data/runs/final
RUN_NAME="$(date -u +%Y%m%dT%H%M%S)_$$"
DATASET_DIR=""
AGENT_MODEL_DIR=""
LLM_MODEL_DIR=""
CONFIG=config/classification.yaml
TARGET=both
SELECTION_ARGS=()
INDEX=""
REPORT=""
DRY_RUN=false
FULL=false
PREDICT_INPUT=""
PREDICT_TARGET=agent
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dry-run) DRY_RUN=true; shift; continue ;;
    --full) FULL=true; shift; continue ;;
    --sites|--agents|--llms|--input-dir|--run-name|--dataset-dir|--agent-model-dir|--llm-model-dir|--config|--target|--index|--report|--predict-input|--predict-target)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "Missing value for $1" ;;
    *) fail "Unknown option: $1" ;;
  esac
  case "$1" in
    --sites|--agents|--llms) SELECTION_ARGS+=("$1" "$2") ;;
    --input-dir) INPUT_DIR="$2" ;;
    --run-name) RUN_NAME="$2" ;;
    --dataset-dir) DATASET_DIR="$2" ;;
    --agent-model-dir) AGENT_MODEL_DIR="$2" ;;
    --llm-model-dir) LLM_MODEL_DIR="$2" ;;
    --config) CONFIG="$2" ;;
    --target) TARGET="$2" ;;
    --index) INDEX="$2" ;;
    --report) REPORT="$2" ;;
    --predict-input) PREDICT_INPUT="$2" ;;
    --predict-target) PREDICT_TARGET="$2" ;;
  esac
  shift 2
done
[[ "$RUN_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail 'Invalid --run-name'
case "$TARGET" in agent|llm|both) ;; *) fail 'Invalid --target' ;; esac
case "$PREDICT_TARGET" in agent|llm) ;; *) fail 'Invalid --predict-target' ;; esac
if $DRY_RUN && [[ "$COMMAND" != prepare ]]; then fail '--dry-run requires prepare'; fi
if $FULL && [[ "$COMMAND" != test ]]; then fail '--full requires test'; fi
if [[ ${#SELECTION_ARGS[@]} -gt 0 && "$COMMAND" != all && "$COMMAND" != dataset && "$COMMAND" != train ]]; then
  fail '--sites/--agents/--llms apply to all, dataset or train; test runs code regression tests'
fi
DATASET_DIR="${DATASET_DIR:-data/datasets/l3_$RUN_NAME}"
AGENT_MODEL_DIR="${AGENT_MODEL_DIR:-data/experiments/l3_${RUN_NAME}_agent}"
LLM_MODEL_DIR="${LLM_MODEL_DIR:-data/experiments/l3_${RUN_NAME}_llm}"
REPORT="${REPORT:-data/results/prepare_$RUN_NAME.json}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"

prepare_runs() {
  local args=(--input-dir "$INPUT_DIR" --report "$REPORT")
  if ! $DRY_RUN; then args+=(--apply); fi
  if [[ -n "$INDEX" ]]; then args+=(--index "$INDEX"); fi
  "$PYTHON" -m analysis.prepare_runs "${args[@]}"
}
build_dataset() {
  "$PYTHON" -m analysis.dataset --input-dir "$INPUT_DIR" --output-dir "$DATASET_DIR" "${SELECTION_ARGS[@]}"
}
train_models() {
  local target output
  local filters=("${SELECTION_ARGS[@]}")
  if [[ "$COMMAND" == all ]]; then filters=(); fi
  for target in agent llm; do
    if [[ "$TARGET" != both && "$TARGET" != "$target" ]]; then continue; fi
    if [[ "$target" == agent ]]; then output="$AGENT_MODEL_DIR"; else output="$LLM_MODEL_DIR"; fi
    echo "Training $target -> $output"
    "$PYTHON" -m analysis.train --config "$CONFIG" --dataset "$DATASET_DIR/samples.json" \
      --output-dir "$output" --target "$target" "${filters[@]}"
  done
}
run_tests() {
  if $FULL; then
    "$PYTHON" -m pytest -q
  else
    "$PYTHON" -m pytest -q test/test_l3_browser_dynamic.py test/test_classification.py \
      test/test_organize_runs.py test/test_prepare_runs.py
  fi
}
predict() {
  [[ -n "$PREDICT_INPUT" ]] || fail 'predict requires --predict-input'
  local model="$AGENT_MODEL_DIR/model.joblib"
  if [[ "$PREDICT_TARGET" == llm ]]; then model="$LLM_MODEL_DIR/model.joblib"; fi
  "$PYTHON" -m analysis.predict --model "$model" --input "$PREDICT_INPUT"
}
case "$COMMAND" in
  prepare) prepare_runs ;;
  dataset) build_dataset ;;
  train) train_models ;;
  test) run_tests ;;
  plot)
    if [[ "$TARGET" != llm ]]; then "$PYTHON" -m analysis.plot_results --model-dir "$AGENT_MODEL_DIR"; fi
    if [[ "$TARGET" != agent ]]; then "$PYTHON" -m analysis.plot_results --model-dir "$LLM_MODEL_DIR"; fi
    ;;
  predict) predict ;;
  all) prepare_runs; build_dataset; train_models; run_tests ;;
esac
if [[ -n "$PREDICT_INPUT" && ( "$COMMAND" == all || "$COMMAND" == train ) ]]; then predict; fi
if [[ "$COMMAND" == all || "$COMMAND" == dataset || "$COMMAND" == train ]]; then
  echo "Run name: $RUN_NAME"
  echo "Dataset: $DATASET_DIR"
  if [[ "$COMMAND" != dataset ]]; then
    if [[ "$TARGET" != llm ]]; then echo "Agent results: $AGENT_MODEL_DIR"; fi
    if [[ "$TARGET" != agent ]]; then echo "LLM results: $LLM_MODEL_DIR"; fi
  fi
fi


# bash scripts/run_analysis.sh prepare --dry-run  # 预览整理
# bash scripts/run_analysis.sh prepare            # 执行整理
# bash scripts/run_analysis.sh all                # 一键执行全部步骤
# bash scripts/run_analysis.sh test               # 回归测试
# 
# # 分步构建和训练
# bash scripts/run_analysis.sh dataset --run-name exp01
# bash scripts/run_analysis.sh train --run-name exp01