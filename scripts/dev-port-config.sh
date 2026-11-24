# 只解析已校验的端口对；真实配置由应用从 SQLite 读取。
read_configured_dev_ports() {
  local result
  local args=("$@")
  [[ -z "${FRONTEND_PORT:-}" ]] || args+=(--frontend "$FRONTEND_PORT")
  [[ -z "${BACKEND_PORT:-}" ]] || args+=(--backend "$BACKEND_PORT")
  result="$(cd "$ROOT" && PYTHONPATH="$ROOT/backend" "$ROOT/.venv/bin/python" -m app.dev_ports --pair "${args[@]}")" || return
  read -r FRONTEND_PORT BACKEND_PORT <<< "$result"
}

read_last_dev_ports() {
  local record="$ROOT/.codex/run-logs/dev-ports"
  local frontend backend rest
  [[ -f "$record" ]] || return 1
  read -r frontend backend rest < "$record" || return 1
  [[ -z "$rest" && "$frontend" =~ ^[0-9]{4,5}$ && "$backend" =~ ^[0-9]{4,5}$ ]] || return 1
  (( 10#$frontend >= 1024 && 10#$frontend <= 65535 && 10#$backend >= 1024 &&
     10#$backend <= 65535 && 10#$frontend != 10#$backend )) || return 1
  if [[ -z "${FRONTEND_PORT:-}" && -z "${BACKEND_PORT:-}" ]]; then
    FRONTEND_PORT="$frontend"
    BACKEND_PORT="$backend"
  else
    return 1
  fi
}
