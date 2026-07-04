#!/usr/bin/env bash
# 用途：Docker Compose 一键管理容器化前后端服务。
# 关键步骤：选择 compose 文件 -> 根据动作执行 up/stop/down/restart/status/logs。
set -euo pipefail

ACTION="${1:-status}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

compose() {
  # 统一封装 docker compose 调用，便于切换源码构建 compose 或 GHCR 镜像 compose。
  docker compose -f "$ROOT/$COMPOSE_FILE" "$@"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "ERROR: docker was not found." >&2
    exit 1
  }
  docker compose version >/dev/null 2>&1 || {
    echo "ERROR: docker compose was not found." >&2
    exit 1
  }
  [[ -f "$ROOT/$COMPOSE_FILE" ]] || {
    echo "ERROR: compose file not found: $COMPOSE_FILE" >&2
    exit 1
  }
}

require_docker

case "$ACTION" in
  start)
    compose up -d
    compose ps
    ;;
  stop)
    compose stop
    compose ps
    ;;
  restart)
    compose restart
    compose ps
    ;;
  down)
    compose down
    ;;
  status)
    compose ps
    ;;
  logs)
    compose logs --tail 120
    ;;
  *)
    echo "Usage: COMPOSE_FILE=docker-compose.yml bash scripts/dev-docker.sh {start|stop|restart|down|status|logs}" >&2
    echo "Example: COMPOSE_FILE=docker-compose.ghcr.yml MEDIAAI_IMAGE_TAG=v0.6.9 bash scripts/dev-docker.sh start" >&2
    exit 2
    ;;
esac
