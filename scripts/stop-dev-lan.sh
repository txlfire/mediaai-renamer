#!/usr/bin/env bash
# 用途：在 Linux/macOS 环境停止局域网开发服务。
# 关键步骤：按 PID 停止 -> 按端口兜底清理 -> 检查端口是否仍在监听。
set -euo pipefail

FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_PORT="${BACKEND_PORT:-8970}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.codex/run-logs"

stop_pid_file() {
  # PID 文件存在时优先按 PID 停止，随后删除 PID 文件。
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
  # 处理 PID 文件缺失或服务异常退出后仍占用端口的情况。
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

# 先精准停止，再按端口兜底清理。
stop_pid_file "$LOG_DIR/frontend.pid"
stop_pid_file "$LOG_DIR/backend.pid"
stop_port "$FRONTEND_PORT"
stop_port "$BACKEND_PORT"
sleep 1

# 若端口仍在监听，说明当前用户可能无权停止对应进程。
if is_listening "$FRONTEND_PORT" || is_listening "$BACKEND_PORT"; then
  echo "WARN: Some ports are still listening. You may need sudo to stop them." >&2
  exit 1
fi

echo "Development services stopped."
