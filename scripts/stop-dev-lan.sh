#!/usr/bin/env bash
# 用途：在 Linux/macOS 环境停止局域网开发服务。
# 只按可验证的项目 PID 停止，端口监听者仅用于提示。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.codex/run-logs"
source "$ROOT/scripts/dev-port-config.sh"

stop_pid_file() {
  # PID 文件存在时优先按 PID 停止，随后删除 PID 文件。
  local file="$1" expected="$2"
  if [[ -f "$file" ]]; then
    local pid command
    pid="$(head -n 1 "$file" || true)"
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
      command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
      if [[ "$command" == *"$expected"* ]]; then
        kill "$pid" >/dev/null 2>&1 || return 1
      fi
    fi
    rm -f "$file"
  fi
}

is_listening() {
  # 停止后检查端口监听状态，用于提示是否需要 sudo 或手动处理。
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" | grep -q ":$port"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -i "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

if [[ "${1:-}" != "--owned-only" ]]; then
  read_last_dev_ports || read_configured_dev_ports
fi
stopFailed=0
stop_pid_file "$LOG_DIR/frontend.pid" "$ROOT/node_modules/vite/bin/vite.js" || stopFailed=1
stop_pid_file "$LOG_DIR/backend.pid" "$ROOT/.venv/bin/python -m uvicorn app.main:app" || stopFailed=1
sleep 1
if (( stopFailed )); then
  echo "WARN: Could not stop one or more managed development processes." >&2
  exit 1
fi

# 若端口仍在监听，说明当前用户可能无权停止对应进程。
if [[ "${1:-}" != "--owned-only" ]] && (is_listening "$FRONTEND_PORT" || is_listening "$BACKEND_PORT"); then
  echo "WARN: Some ports are still listening. Unknown owners were not stopped." >&2
  exit 1
fi

echo "Development services stopped."
