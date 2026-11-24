#!/usr/bin/env bash
# 用途：在 Linux/macOS 环境启动局域网开发服务，同时启动 FastAPI 后端和 Vite 前端。
# 关键步骤：检查依赖 -> 清理旧进程/端口 -> 后台启动服务 -> 写入 PID 和日志。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.codex/run-logs"
PYTHON="$ROOT/.venv/bin/python"
VITE_ENTRY="$ROOT/node_modules/vite/bin/vite.js"
source "$ROOT/scripts/dev-port-config.sh"

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

require_command node "Install Node.js, then run npm ci."
require_command npm "Install npm, then run npm ci."
require_file "$PYTHON" "Create the virtual environment and install backend dependencies: python3 -m venv .venv && .venv/bin/pip install --require-hashes -r backend/requirements.lock"
require_file "$ROOT/backend/app/main.py" "Run this script from the project root."
require_file "$ROOT/backend/requirements.txt" "Backend dependency manifest is missing."
require_file "$ROOT/backend/requirements.lock" "Backend dependency lock is missing."
require_file "$VITE_ENTRY" "Frontend dependencies are missing. Run npm ci."

# 验证后端关键依赖已安装，避免 uvicorn 启动后立即退出。
"$PYTHON" -c "import uvicorn, fastapi" >/dev/null 2>&1 || fail "Backend Python dependencies are incomplete. Run .venv/bin/pip install --require-hashes -r backend/requirements.lock"

mkdir -p "$LOG_DIR"
read_configured_dev_ports
# 只停止本项目已有进程；目标端口冲突时不结束其他进程。
bash "$ROOT/scripts/stop-dev-lan.sh" --owned-only
read_configured_dev_ports --check-bind

cd "$ROOT"
bindHost="0.0.0.0"
[[ "${MEDIAAI_DEV_LOOPBACK_ONLY:-}" != "1" ]] || bindHost="127.0.0.1"

# 后端后台启动，输出和错误日志分开写入 .codex/run-logs。
PYTHONPATH=backend CI=true MEDIAAI_DEV_FRONTEND_PORT="$FRONTEND_PORT" MEDIAAI_DEV_BACKEND_PORT="$BACKEND_PORT" \
  "$PYTHON" -m uvicorn app.main:app --host "$bindHost" --port "$BACKEND_PORT" --reload --app-dir backend >"$LOG_DIR/backend-uvicorn.out.log" 2>"$LOG_DIR/backend-uvicorn.err.log" &
BACKEND_PID="$!"
echo "$BACKEND_PID" >"$LOG_DIR/backend.pid"

# 前端后台启动，直接调用本地 Vite 入口，确保使用项目依赖。
CI=true VITE_BACKEND_URL="http://127.0.0.1:$BACKEND_PORT" node "$VITE_ENTRY" --host "$bindHost" --port "$FRONTEND_PORT" --strictPort --config frontend/vite.config.ts >"$LOG_DIR/frontend-vite.out.log" 2>"$LOG_DIR/frontend-vite.err.log" &
FRONTEND_PID="$!"
echo "$FRONTEND_PID" >"$LOG_DIR/frontend.pid"
if ! read_configured_dev_ports --wait-ready; then
  bash "$ROOT/scripts/stop-dev-lan.sh" --owned-only || true
  fail "Development services did not become healthy. Check $LOG_DIR."
fi
printf '%s %s\n' "$FRONTEND_PORT" "$BACKEND_PORT" >"$LOG_DIR/dev-ports"

echo "Backend started. PID: $BACKEND_PID, URL: http://127.0.0.1:$BACKEND_PORT/api/health"
echo "Frontend started. PID: $FRONTEND_PID, URL: http://127.0.0.1:$FRONTEND_PORT"
if [[ "$bindHost" == "0.0.0.0" ]]; then
  echo "LAN URL: http://<this-machine-lan-ip>:$FRONTEND_PORT"
fi
echo "Logs: $LOG_DIR"
