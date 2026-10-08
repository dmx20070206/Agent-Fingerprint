#!/usr/bin/env bash

# Shared Agent-E service lifecycle for task scripts.

AGENTE_SERVICE_PID=${AGENTE_SERVICE_PID:-}
AGENTE_SERVICE_LOG=${AGENTE_SERVICE_LOG:-}
AGENTE_BROWSER_DIR=${AGENTE_BROWSER_DIR:-}

# Each task script should start with an isolated Agent-E process/profile.  Set
# this to 0 only when deliberately sharing a long-lived service is required.
AGENTE_FRESH_START=${AGENTE_FRESH_START:-1}

agente_service_ready() {
    local endpoint=${1:?Agent-E endpoint is required}
    local base_url=${endpoint%/execute_task}
    local openapi

    openapi=$(curl --noproxy '*' --silent --show-error --fail --max-time 2 \
        "$base_url/openapi.json" 2>/dev/null) || return 1
    [[ "$openapi" == *'"/execute_task"'* ]]
}

find_conda() {
    if command -v conda >/dev/null 2>&1; then
        command -v conda
        return
    fi

    local candidate
    for candidate in "$HOME/anaconda3/bin/conda" "$HOME/miniconda3/bin/conda"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done

    return 1
}

start_agente_service() {
    local endpoint=${1:?Agent-E endpoint is required}
    local repo_root=${2:?Repository root is required}

    # A previous invocation may have been interrupted before its EXIT trap ran.
    # Remove only our own temporary browser profiles; never touch user data.
    if [[ -d "${TMPDIR:-/tmp}" ]]; then
        find "${TMPDIR:-/tmp}" -maxdepth 1 -type d \
            -name 'agent-fingerprint-agente-browser.*' -exec find {} -depth -delete \; \
            2>/dev/null || true
    fi

    # Do not inherit an already-running local Agent-E service/profile.  This is
    # intentionally limited to the default local endpoint used by these task
    # scripts.  Custom endpoints remain shareable unless explicitly changed.
    if [[ "${AGENTE_FRESH_START}" == 1 ]] && agente_service_ready "$endpoint"; then
        if [[ "$endpoint" == http://127.0.0.1:8080/execute_task || \
              "$endpoint" == http://localhost:8080/execute_task ]]; then
            pkill -TERM -f 'uvicorn ae\.server\.api_routes:app' 2>/dev/null || true
            for _ in {1..30}; do
                agente_service_ready "$endpoint" || break
                sleep 0.1
            done
        fi
    fi

    if agente_service_ready "$endpoint"; then
        return
    fi

    case "$endpoint" in
        http://127.0.0.1:8080/execute_task|http://localhost:8080/execute_task)
            ;;
        *)
            printf 'Agent-E endpoint is unavailable and cannot be started locally: %s\n' "$endpoint" >&2
            return 1
            ;;
    esac

    local conda_bin
    if ! conda_bin=$(find_conda); then
        printf 'Cannot start Agent-E: conda was not found in PATH, ~/anaconda3, or ~/miniconda3.\n' >&2
        return 1
    fi

    local agent_env=${AGENTE_CONDA_ENV:-agent-e}
    local agent_repo="$repo_root/third_party/Agent-E"
    if [[ ! -d "$agent_repo/ae" ]]; then
        printf 'Cannot start Agent-E: source directory does not exist: %s\n' "$agent_repo" >&2
        return 1
    fi

    AGENTE_SERVICE_LOG=$(mktemp "${TMPDIR:-/tmp}/agent-fingerprint-agente.XXXXXX.log")
    AGENTE_BROWSER_DIR=$(mktemp -d "${TMPDIR:-/tmp}/agent-fingerprint-agente-browser.XXXXXX")
    setsid bash -c 'cd -- "$1" && export BROWSER_STORAGE_DIR="$4" && exec "$2" run -n "$3" --no-capture-output uvicorn ae.server.api_routes:app --host 127.0.0.1 --port 8080 --loop asyncio' \
        _ "$agent_repo" "$conda_bin" "$agent_env" "$AGENTE_BROWSER_DIR" \
        >"$AGENTE_SERVICE_LOG" 2>&1 &
    AGENTE_SERVICE_PID=$!

    printf 'Starting Agent-E service on http://127.0.0.1:8080 ...\n'
    local attempt
    for attempt in {1..120}; do
        if agente_service_ready "$endpoint"; then
            printf 'Agent-E service is ready.\n'
            return
        fi
        if ! kill -0 "$AGENTE_SERVICE_PID" 2>/dev/null; then
            printf 'Agent-E service exited during startup.\n' >&2
            sed -n '1,200p' "$AGENTE_SERVICE_LOG" >&2
            return 1
        fi
        sleep 0.5
    done

    printf 'Timed out waiting for Agent-E service.\n' >&2
    sed -n '1,200p' "$AGENTE_SERVICE_LOG" >&2
    return 1
}

stop_agente_service() {
    if [[ -n "${AGENTE_SERVICE_PID:-}" ]]; then
        kill -TERM -- "-$AGENTE_SERVICE_PID" 2>/dev/null || \
            kill -TERM "$AGENTE_SERVICE_PID" 2>/dev/null || true

        local attempt
        for attempt in {1..50}; do
            kill -0 -- "-$AGENTE_SERVICE_PID" 2>/dev/null || break
            sleep 0.1
        done
        if kill -0 -- "-$AGENTE_SERVICE_PID" 2>/dev/null; then
            kill -KILL -- "-$AGENTE_SERVICE_PID" 2>/dev/null || \
                kill -KILL "$AGENTE_SERVICE_PID" 2>/dev/null || true
        fi
        wait "$AGENTE_SERVICE_PID" 2>/dev/null || true
        AGENTE_SERVICE_PID=
    fi

    if [[ -n "${AGENTE_SERVICE_LOG:-}" ]]; then
        rm -f -- "$AGENTE_SERVICE_LOG"
        AGENTE_SERVICE_LOG=
    fi

    if [[ -n "${AGENTE_BROWSER_DIR:-}" && -d "$AGENTE_BROWSER_DIR" ]]; then
        case "$AGENTE_BROWSER_DIR" in
            "${TMPDIR:-/tmp}"/agent-fingerprint-agente-browser.*)
                find "$AGENTE_BROWSER_DIR" -depth -delete
                ;;
        esac
        AGENTE_BROWSER_DIR=
    fi
}

cleanup_agente_service() {
    local status=${1:-0}

    if (( status != 0 )) && [[ -n "${AGENTE_SERVICE_PID:-}" && -s "${AGENTE_SERVICE_LOG:-}" ]]; then
        printf '\nAgent-E service log (last 200 lines):\n' >&2
        tail -n 200 "$AGENTE_SERVICE_LOG" >&2
    fi

    stop_agente_service
    return "$status"
}

ensure_agente_service() {
    local endpoint=${1:?Agent-E endpoint is required}
    local repo_root=${2:?Repository root is required}

    start_agente_service "$endpoint" "$repo_root" || {
        stop_agente_service
        return 1
    }
}
