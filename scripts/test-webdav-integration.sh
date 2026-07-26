#!/usr/bin/env bash
# 用途：在 Linux、fnOS 或 GitHub Actions 中启动隔离的 HTTPS WebDAV 容器并执行真实协议集成测试。
# 关键步骤：检查 Docker -> 清理固定测试目录 -> 启动容器 -> 等待健康 -> 执行测试 -> 销毁容器和临时数据。
set -euo pipefail

scriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "${scriptDir}/.." && pwd)"
composeDir="${root}/tests/integration/webdav"
composeFile="${composeDir}/docker-compose.yml"
tempRoot="${composeDir}/.tmp"

command -v docker >/dev/null 2>&1 || {
  echo "未找到 Docker，请在安装 Docker 的 Linux、fnOS 或 GitHub Actions 环境执行。" >&2
  exit 1
}
docker compose version >/dev/null

export MEDIAAI_WEBDAV_TEST_USERNAME="${MEDIAAI_WEBDAV_TEST_USERNAME:-mediaai-test}"
export MEDIAAI_WEBDAV_TEST_PASSWORD="${MEDIAAI_WEBDAV_TEST_PASSWORD:-mediaai-test-only-password}"

case "${tempRoot}" in
  "${composeDir}"/.tmp) ;;
  *)
    echo "WebDAV 测试临时目录不在预期工作区内：${tempRoot}" >&2
    exit 1
    ;;
esac

cleanup() {
  docker compose -f "${composeFile}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf -- "${tempRoot}"
}
trap cleanup EXIT

rm -rf -- "${tempRoot}"
mkdir -p "${tempRoot}/certs" "${tempRoot}/data"

cd "${root}"
docker compose -f "${composeFile}" up -d --build

healthy="false"
for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{.State.Health.Status}}' mediaai-webdav-test 2>/dev/null || true)"
  if [[ "${status}" == "healthy" ]]; then
    healthy="true"
    break
  fi
  sleep 1
done
if [[ "${healthy}" != "true" ]]; then
  docker compose -f "${composeFile}" logs --no-color webdav
  echo "WebDAV 测试容器未在 60 秒内进入健康状态。" >&2
  exit 1
fi

export MEDIAAI_WEBDAV_INTEGRATION="1"
export MEDIAAI_WEBDAV_TEST_URL="https://localhost:9443"
export MEDIAAI_WEBDAV_TEST_CA_CERT="${tempRoot}/certs/ca.crt"
export PYTHONPATH="backend"

if [[ -x "${root}/.venv/bin/python" ]]; then
  pythonBin="${root}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  pythonBin="python3"
else
  pythonBin="python"
fi

"${pythonBin}" -m unittest backend.tests.integration.test_webdav_e2e -v
