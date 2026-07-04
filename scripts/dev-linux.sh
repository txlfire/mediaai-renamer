#!/usr/bin/env bash
# 用途：Linux/macOS 一键管理本地开发服务。
# 关键步骤：根据第一个参数判断动作 -> 调用现有启停脚本 -> 输出前后端状态。
set -euo pipefail

ACTION="${1:-status}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_PORT="${BACKEND_PORT:-8970}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

is_listening() {
  # 仅检查端口监听状态，不读取业务数据。
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" | grep -q ":$port"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -i "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

show_status() {
  if is_listening "$BACKEND_PORT"; then
    echo "Backend: running, http://127.0.0.1:$BACKEND_PORT/api/health"
  else
    echo "Backend: stopped"
  fi

  if is_listening "$FRONTEND_PORT"; then
    echo "Frontend: running, http://127.0.0.1:$FRONTEND_PORT"
  else
    echo "Frontend: stopped"
  fi
}

case "$ACTION" in
  start)
    FRONTEND_PORT="$FRONTEND_PORT" BACKEND_PORT="$BACKEND_PORT" bash "$ROOT/scripts/start-dev-lan.sh"
    show_status
    ;;
  stop)
    FRONTEND_PORT="$FRONTEND_PORT" BACKEND_PORT="$BACKEND_PORT" bash "$ROOT/scripts/stop-dev-lan.sh"
    show_status
    ;;
  restart)
    FRONTEND_PORT="$FRONTEND_PORT" BACKEND_PORT="$BACKEND_PORT" bash "$ROOT/scripts/stop-dev-lan.sh"
    FRONTEND_PORT="$FRONTEND_PORT" BACKEND_PORT="$BACKEND_PORT" bash "$ROOT/scripts/start-dev-lan.sh"
    show_status
    ;;
  status)
    show_status
    ;;
  *)
    echo "Usage: bash scripts/dev-linux.sh {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
