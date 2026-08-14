#!/usr/bin/env bash
# Start LZCore backend + frontend on macOS/Linux.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CONFIG_ALLOWED_ORIGINS=""
if [ -f "$ROOT/.env.local" ]; then
    CONFIG_ALLOWED_ORIGINS="$(sed -n 's/^LZCORE_ALLOWED_ORIGINS=//p' "$ROOT/.env.local" | tail -n 1)"
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env.local"
    set +a
fi
BACKEND_PORT="${BACKEND_PORT:-8011}"
FRONTEND_PORT="${FRONTEND_PORT:-5273}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_MODE="${FRONTEND_MODE:-preview}"
INSTALL_DEPS="${INSTALL_DEPS:-auto}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
BACKEND_PID_FILE="$ROOT/.backend.pid"
FRONTEND_PID_FILE="$ROOT/.frontend.pid"
BACKEND_SCREEN="${BACKEND_SCREEN:-lzcore-backend}"
FRONTEND_SCREEN="${FRONTEND_SCREEN:-lzcore-frontend}"

log() { printf '%s\n' "$*"; }
fail() { log "[ERROR] $*" >&2; exit 1; }

find_cmd() {
    local name="$1"
    shift
    local candidate
    for candidate in "$@"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    command -v "$name" 2>/dev/null || return 1
}

PYTHON_BIN="${PYTHON_BIN:-$(find_cmd python3 "$HOME/.local/bin/python3" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3)}"
NODE_BIN="${NODE_BIN:-$(find_cmd node "$HOME/.local/node/bin/node" /opt/homebrew/bin/node /usr/local/bin/node /usr/bin/node)}"
NPM_BIN="${NPM_BIN:-$(find_cmd npm "$HOME/.local/node/bin/npm" /opt/homebrew/bin/npm /usr/local/bin/npm /usr/bin/npm)}"
VITE_BIN="${VITE_BIN:-$ROOT/frontend/node_modules/.bin/vite}"
# This policy is intentionally kept outside the launcher so tests can exercise
# it without starting services or binding ports.
# shellcheck source=scripts/startup_security.sh
. "$ROOT/scripts/startup_security.sh"

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
            # Match any reasonable Python invocation of backend/main.py
            printf '%s' "$command_line" | grep -qE 'python[0-9.]* (.*\/)?backend/main\.py' || return 1
            [ "$cwd" = "$ROOT" ]
            ;;
        frontend)
            # Match any node invocation of vite
            printf '%s' "$command_line" | grep -qE 'node .*/vite' || return 1
            [ "$cwd" = "$ROOT/frontend" ]
            ;;
        *) return 1 ;;
    esac
}

port_pids() {
    lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

port_pid() {
    port_pids "$1" | head -n 1
}

screen_exists() {
    command -v screen >/dev/null 2>&1 && screen -ls 2>/dev/null | grep -q "[.]$1[[:space:]]"
}

stop_screen() {
    local name="$1"
    if command -v screen >/dev/null 2>&1; then
        screen -S "$name" -X quit >/dev/null 2>&1 || true
    fi
}

# Track which services we successfully started so a later failure can
# roll them back instead of leaving half-up processes behind.
STARTED_SERVICES=()

stop_started_services() {
    # Roll back any service that already came up. Called on fatal
    # failures so we never leave a half-started stack holding ports.
    local svc
    for svc in "${STARTED_SERVICES[@]+"${STARTED_SERVICES[@]}"}"; do
        case "$svc" in
            backend)
                log "[rollback] stopping backend (port $BACKEND_PORT)"
                stop_screen "$BACKEND_SCREEN"
                if [ -f "$BACKEND_PID_FILE" ]; then
                    local pid
                    pid="$(cat "$BACKEND_PID_FILE" 2>/dev/null || true)"
                    if [ -n "${pid:-}" ]; then
                        kill "$pid" >/dev/null 2>&1 || true
                    fi
                    rm -f "$BACKEND_PID_FILE"
                fi
                ;;
            frontend)
                log "[rollback] stopping frontend (port $FRONTEND_PORT)"
                stop_screen "$FRONTEND_SCREEN"
                if [ -f "$FRONTEND_PID_FILE" ]; then
                    local pid
                    pid="$(cat "$FRONTEND_PID_FILE" 2>/dev/null || true)"
                    if [ -n "${pid:-}" ]; then
                        kill "$pid" >/dev/null 2>&1 || true
                    fi
                    rm -f "$FRONTEND_PID_FILE"
                fi
                ;;
        esac
    done
}

wait_for_url() {
    local role="$1"
    local url="$2"
    local attempts="${3:-40}"
    local i=1
    while [ "$i" -le "$attempts" ]; do
        if curl --fail --silent --show-error --max-time 2 "$url" >/dev/null 2>&1; then
            log "[$role] Ready."
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

ensure_port_available_or_owned() {
    local role="$1"
    local port="$2"
    local pid
    pid="$(port_pid "$port")"
    [ -n "$pid" ] || return 0
    if process_belongs_to_project "$pid" "$role"; then
        log "[$role] Already running on port $port (PID $pid)."
        return 2
    fi
    fail "Port $port is occupied by another process (PID $pid)."
}

write_port_pid() {
    local port="$1"
    local file="$2"
    local pid
    pid="$(port_pid "$port")"
    [ -n "$pid" ] || return 1
    printf '%s\n' "$pid" > "$file"
}

check_version() {
    [ -x "$PYTHON_BIN" ] || fail "Python 3.12+ is required."
    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
        || fail "Python 3.12+ is required; found $("$PYTHON_BIN" --version 2>&1)."

    "$PYTHON_BIN" -c 'import pip' >/dev/null 2>&1 || fail "Python pip is required (ensure pip is installed)."

    [ -x "$NODE_BIN" ] || fail "Node.js 24 LTS is required."
    "$NODE_BIN" -e 'const major=Number(process.versions.node.split(".")[0]); process.exit(major===24 ? 0 : 1)' \
        || fail "Node.js 24 LTS is required; found $("$NODE_BIN" --version 2>&1)."
    [ -x "$NPM_BIN" ] || fail "npm is required."
    command -v curl >/dev/null 2>&1 || fail "curl is required."
    command -v lsof >/dev/null 2>&1 || fail "lsof is required."
}

detect_venv() {
    # Prefer project .venv over global Python.
    local venv_python="$ROOT/.venv/bin/python3"
    if [ -x "$venv_python" ]; then
        local venv_ver
        venv_ver="$("$venv_python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")"
        if [ -n "$venv_ver" ]; then
            local major minor
            major="$(echo "$venv_ver" | cut -d. -f1)"
            minor="$(echo "$venv_ver" | cut -d. -f2)"
            if [ "$major" -ge 3 ] && [ "$minor" -ge 12 ] 2>/dev/null; then
                PYTHON_BIN="$venv_python"
                return 0
            fi
        fi
    fi
    return 1
}

install_dependencies() {
    if [ "$INSTALL_DEPS" = "0" ] || [ "$INSTALL_DEPS" = "false" ]; then
        log "[deps] Skipped (INSTALL_DEPS=$INSTALL_DEPS)."
        return
    fi

    log "[deps] Checking Python dependencies..."
    if ! "$PYTHON_BIN" -c 'import flask, flask_sock, yaml, bs4, pdfplumber, scapy' >/dev/null 2>&1; then
        "$PYTHON_BIN" -m pip install -r "$ROOT/requirements.txt" || fail "Failed to install Python dependencies."
    fi
    "$PYTHON_BIN" -m pip check >/dev/null || fail "Python dependency check failed."

    log "[deps] Checking frontend dependencies..."
    if [ ! -x "$VITE_BIN" ]; then
        (cd "$ROOT/frontend" && "$NPM_BIN" install) || fail "Failed to install frontend dependencies."
    fi
}

local_ips() {
    if command -v ifconfig >/dev/null 2>&1; then
        ifconfig | awk '/inet / && $2 != "127.0.0.1" {print $2}'
    elif command -v hostname >/dev/null 2>&1; then
        hostname -I 2>/dev/null | tr ' ' '\n' | sed '/^$/d'
    fi
}

start_backend() {
    local state
    ensure_port_available_or_owned backend "$BACKEND_PORT" || state=$?
    if [ "${state:-0}" = "2" ]; then
        write_port_pid "$BACKEND_PORT" "$BACKEND_PID_FILE"
        return
    fi

    # Build allowed origins from all local IPs so LAN access works
    local allowed_origins="http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT,http://[::1]:$FRONTEND_PORT"
    for ip in $(local_ips); do
        allowed_origins="$allowed_origins,http://$ip:$FRONTEND_PORT"
    done
    if [ -n "$CONFIG_ALLOWED_ORIGINS" ]; then
        allowed_origins="$allowed_origins,$CONFIG_ALLOWED_ORIGINS"
    fi
    export LZCORE_ALLOWED_ORIGINS="$allowed_origins"
    export LZCORE_RUNTIME_BIND_HOST="$BACKEND_HOST"

    log "[backend] Starting on $BACKEND_HOST:$BACKEND_PORT..."
    : > "$LOG_DIR/backend-$BACKEND_PORT.log"
    stop_screen "$BACKEND_SCREEN"
    if command -v screen >/dev/null 2>&1; then
        screen -dmS "$BACKEND_SCREEN" /bin/bash -lc \
            "cd '$ROOT' && export LZCORE_ALLOWED_ORIGINS='$allowed_origins' && exec '$PYTHON_BIN' backend/main.py --host '$BACKEND_HOST' --port '$BACKEND_PORT' >> '$LOG_DIR/backend-$BACKEND_PORT.log' 2>&1"
    else
        (cd "$ROOT" && export LZCORE_ALLOWED_ORIGINS="$allowed_origins" && nohup "$PYTHON_BIN" backend/main.py --host "$BACKEND_HOST" --port "$BACKEND_PORT" >> "$LOG_DIR/backend-$BACKEND_PORT.log" 2>&1 </dev/null &)
    fi
    wait_for_url backend "http://127.0.0.1:$BACKEND_PORT/api/health" || { stop_started_services; fail "Backend failed to start. See $LOG_DIR/backend-$BACKEND_PORT.log"; }
    write_port_pid "$BACKEND_PORT" "$BACKEND_PID_FILE"
    STARTED_SERVICES+=("backend")
}

start_frontend() {
    local state
    ensure_port_available_or_owned frontend "$FRONTEND_PORT" || state=$?
    if [ "${state:-0}" = "2" ]; then
        write_port_pid "$FRONTEND_PORT" "$FRONTEND_PID_FILE"
        return
    fi

    local vite_args=("preview" "--host" "$FRONTEND_HOST" "--port" "$FRONTEND_PORT")
    if [ "$FRONTEND_MODE" = "dev" ]; then
        vite_args=("--host" "$FRONTEND_HOST" "--port" "$FRONTEND_PORT")
    else
        log "[frontend] Building production assets..."
        (cd "$ROOT/frontend" && "$NPM_BIN" run build) || fail "Frontend production build failed."
    fi

    log "[frontend] Starting in $FRONTEND_MODE mode on $FRONTEND_HOST:$FRONTEND_PORT..."
    local dev_api_target="http://127.0.0.1:$BACKEND_PORT"
    local vite_command
    printf -v vite_command ' %q' "${vite_args[@]}"
    : > "$LOG_DIR/frontend-$FRONTEND_PORT.log"
    stop_screen "$FRONTEND_SCREEN"
    if command -v screen >/dev/null 2>&1; then
        screen -dmS "$FRONTEND_SCREEN" /bin/bash -lc \
            "cd '$ROOT/frontend' && export VITE_DEV_API_TARGET='$dev_api_target' && exec '$VITE_BIN'$vite_command >> '$LOG_DIR/frontend-$FRONTEND_PORT.log' 2>&1"
    else
        (cd "$ROOT/frontend" && export VITE_DEV_API_TARGET="$dev_api_target" && nohup "$VITE_BIN" "${vite_args[@]}" >> "$LOG_DIR/frontend-$FRONTEND_PORT.log" 2>&1 </dev/null &)
    fi
    wait_for_url frontend "http://127.0.0.1:$FRONTEND_PORT" || { stop_started_services; fail "Frontend failed to start. See $LOG_DIR/frontend-$FRONTEND_PORT.log"; }
    write_port_pid "$FRONTEND_PORT" "$FRONTEND_PID_FILE"
    STARTED_SERVICES+=("frontend")
}

print_summary() {
    log ""
    log "Backend API:  http://127.0.0.1:$BACKEND_PORT"
    log "Frontend UI:  http://127.0.0.1:$FRONTEND_PORT"
    for ip in $(local_ips); do
        log "LAN UI:       http://$ip:$FRONTEND_PORT"
        log "LAN backend:  http://$ip:$BACKEND_PORT"
    done
    if command -v screen >/dev/null 2>&1; then
        log ""
        log "Screen sessions: $BACKEND_SCREEN, $FRONTEND_SCREEN"
    fi
    log "Logs:         $LOG_DIR/backend-$BACKEND_PORT.log"
    log "              $LOG_DIR/frontend-$FRONTEND_PORT.log"
    log "Stop with:    ./stop.sh"
}

main() {
    log "LZCore"
    startup_security_validate_network_exposure "$BACKEND_HOST" "$FRONTEND_HOST" || fail "Unsafe network startup configuration."
    mkdir -p "$LOG_DIR"
    detect_venv || true
    check_version
    install_dependencies
    start_backend
    start_frontend
    print_summary
}

main "$@"
