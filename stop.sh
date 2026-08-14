#!/usr/bin/env bash
# Stop LZCore backend + frontend on macOS/Linux.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8011}"
FRONTEND_PORT="${FRONTEND_PORT:-5273}"
BACKEND_PID_FILE="$ROOT/.backend.pid"
FRONTEND_PID_FILE="$ROOT/.frontend.pid"
BACKEND_SCREEN="${BACKEND_SCREEN:-lzcore-backend}"
FRONTEND_SCREEN="${FRONTEND_SCREEN:-lzcore-frontend}"

process_cwd() {
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

process_belongs_to_project() {
    local pid="$1"
    local role="$2"
    local command_line cwd
    kill -0 "$pid" 2>/dev/null || return 1
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    cwd="$(process_cwd "$pid")"

    case "$role" in
        backend)
            printf '%s' "$command_line" | grep -qE 'python[0-9.]* (.*\/)?backend/main\.py' || return 1
            [ "$cwd" = "$ROOT" ]
            ;;
        frontend)
            printf '%s' "$command_line" | grep -qE 'node .*/vite' || return 1
            [ "$cwd" = "$ROOT/frontend" ]
            ;;
        *) return 1 ;;
    esac
}

port_pids() {
    lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

wait_until_stopped() {
    local pid="$1"
    local i=1
    while [ "$i" -le 10 ]; do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 1
        i=$((i + 1))
    done
    return 1
}

stop_screen() {
    local name="$1"
    if command -v screen >/dev/null 2>&1; then
        screen -S "$name" -X quit >/dev/null 2>&1 || true
    fi
}

stop_pid() {
    local role="$1"
    local pid="$2"
    if ! process_belongs_to_project "$pid" "$role"; then
        return 1
    fi
    kill "$pid" 2>/dev/null || true
    if ! wait_until_stopped "$pid"; then
        if process_belongs_to_project "$pid" "$role"; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    fi
    return 0
}

stop_service() {
    local role="$1"
    local port="$2"
    local pid_file="$3"
    local screen_name="$4"
    local stopped=0
    local pid

    if [ -f "$pid_file" ]; then
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        if [ -n "$pid" ] && stop_pid "$role" "$pid"; then
            printf '[%s] Stopped PID %s.\n' "$role" "$pid"
            stopped=1
        fi
    fi

    for pid in $(port_pids "$port"); do
        if stop_pid "$role" "$pid"; then
            printf '[%s] Stopped listener PID %s.\n' "$role" "$pid"
            stopped=1
        else
            printf '[%s] Refusing to stop unverified process on port %s (PID %s).\n' "$role" "$port" "$pid" >&2
        fi
    done

    stop_screen "$screen_name"
    rm -f "$pid_file"

    if [ "$stopped" = "0" ]; then
        printf '[%s] Not running.\n' "$role"
    fi
}

status=0
stop_service frontend "$FRONTEND_PORT" "$FRONTEND_PID_FILE" "$FRONTEND_SCREEN" || status=1
stop_service backend "$BACKEND_PORT" "$BACKEND_PID_FILE" "$BACKEND_SCREEN" || status=1
exit "$status"
