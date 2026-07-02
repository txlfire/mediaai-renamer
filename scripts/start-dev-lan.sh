#!/usr/bin/env bash
# 用途：在 Linux/macOS 环境启动局域网开发服务，同时启动 FastAPI 后端和 Vite 前端。
# 关键步骤：检查依赖 -> 清理旧进程/端口 -> 后台启动服务 -> 写入 PID 和日志。
set -euo pipefail

FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_PORT="${BACKEND_PORT:-8970}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.codex/run-logs"
PYTHON="$ROOT/.venv/bin/python"
VITE_ENTRY="$ROOT/node_modules/vite/bin/vite.js"

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

require_command() {
  # 检查命令是否存在，不满足时给出安装提示。
  command -v "$1" >/dev/null 2>&1 || fail "$1 was not found. $2"
}

require_file() {
  # 检查项目依赖文件是否存在，避免启动后才暴露缺依赖问题。
  [[ -e "$1" ]] || fail "$1 was not found. $2"
}

stop_pid_file() {
  # 优先按 PID 文件停止旧服务，停止后删除 PID 文件避免误用。
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
  # 兜底清理端口占用，优先使用 lsof，其次使用 fuser。
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$port" | xargs -r kill -9
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
  else
    echo "WARN: lsof/fuser not found; cannot automatically clear port $port." >&2
  fi
}

require_command node "Install Node.js, then run npm install."
require_command npm "Install npm, then run npm install."
require_file "$PYTHON" "Create the virtual environment and install backend dependencies: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
require_file "$ROOT/backend/app/main.py" "Run this script from the project root."
require_file "$ROOT/backend/requirements.txt" "Backend dependency manifest is missing."
require_file "$VITE_ENTRY" "Frontend dependencies are missing. Run npm install."

# 验证后端关键依赖已安装，避免 uvicorn 启动后立即退出。
"$PYTHON" -c "import uvicorn, fastapi" >/dev/null 2>&1 || fail "Backend Python dependencies are incomplete. Run .venv/bin/pip install -r backend/requirements.txt"

mkdir -p "$LOG_DIR"
# 启动前清理旧服务，保证端口和 PID 文件处于一致状态。
stop_pid_file "$LOG_DIR/frontend.pid"
stop_pid_file "$LOG_DIR/backend.pid"
stop_port "$FRONTEND_PORT"
stop_port "$BACKEND_PORT"

cd "$ROOT"

# 后端后台启动，输出和错误日志分开写入 .codex/run-logs。
PYTHONPATH=backend CI=true "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload --app-dir backend >"$LOG_DIR/backend-uvicorn.out.log" 2>"$LOG_DIR/backend-uvicorn.err.log" &
BACKEND_PID="$!"
echo "$BACKEND_PID" >"$LOG_DIR/backend.pid"

# 前端后台启动，直接调用本地 Vite 入口，确保使用项目依赖。
CI=true node "$VITE_ENTRY" --host 0.0.0.0 --port "$FRONTEND_PORT" --config frontend/vite.config.ts >"$LOG_DIR/frontend-vite.out.log" 2>"$LOG_DIR/frontend-vite.err.log" &
FRONTEND_PID="$!"
echo "$FRONTEND_PID" >"$LOG_DIR/frontend.pid"

echo "Backend started. PID: $BACKEND_PID, URL: http://127.0.0.1:$BACKEND_PORT/api/health"
echo "Frontend started. PID: $FRONTEND_PID, URL: http://127.0.0.1:$FRONTEND_PORT"
echo "LAN URL: http://<this-machine-lan-ip>:$FRONTEND_PORT"
echo "Logs: $LOG_DIR"
