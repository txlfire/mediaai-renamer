#!/usr/bin/env bash
set -euo pipefail

FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_PORT="${BACKEND_PORT:-8970}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.codex/run-logs"

stop_pid_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(head -n 1 "$file" || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$file"
  fi
}

stop_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$port" | xargs -r kill -9
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
  else
    echo "WARN: lsof/fuser not found; cannot automatically clear port $port." >&2
  fi
}

is_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" | grep -q ":$port"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -i "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

stop_pid_file "$LOG_DIR/frontend.pid"
stop_pid_file "$LOG_DIR/backend.pid"
stop_port "$FRONTEND_PORT"
stop_port "$BACKEND_PORT"
sleep 1

if is_listening "$FRONTEND_PORT" || is_listening "$BACKEND_PORT"; then
  echo "WARN: Some ports are still listening. You may need sudo to stop them." >&2
  exit 1
fi

echo "Development services stopped."
