"""开发启动器共用的启动前端口查询，不导入 ASGI 应用。"""

import argparse
import json
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from app.core.config import load_settings
from app.service.dev_port_service import DevPortError, checkDevPortsAvailable, resolveDevPorts


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def waitForDevServices(ports, *, timeoutSeconds=25):
    # 仅探活回环，不使用系统代理或跟随跳转。
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    urls = ((ports.backendPort, "/api/health"), (ports.frontendPort, "/"), (ports.frontendPort, "/api/health"))
    deadline = time.monotonic() + timeoutSeconds
    for port, resource in urls:
        while time.monotonic() < deadline:
            try:
                wait = min(1, max(0.05, deadline - time.monotonic()))
                with opener.open(f"http://127.0.0.1:{port}{resource}", timeout=wait) as response:
                    if response.status == 200 and (resource == "/" or json.loads(response.read(2048)).get("status") == "ok"):
                        break
            except (OSError, ValueError, AttributeError):
                pass
            time.sleep(0.1)
        else:
            raise DevPortError("dev_services_not_ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=int)
    parser.add_argument("--backend", type=int)
    parser.add_argument("--check-bind", action="store_true")
    parser.add_argument("--wait-ready", action="store_true")
    parser.add_argument("--pair", action="store_true")
    args = parser.parse_args()
    try:
        ports = resolveDevPorts(load_settings(), args.frontend, args.backend)
        if args.check_bind:
            checkDevPortsAvailable(ports)
        if args.wait_ready:
            waitForDevServices(ports)
    except DevPortError as exc:
        parser.exit(1, f"{exc.code}\n")
    except (OSError, ValueError, TypeError):
        parser.exit(1, "service_ports_application_config_unavailable\n")
    if args.pair:
        print(f"{ports.frontendPort} {ports.backendPort}")
    else:
        print(json.dumps(ports.payload()))


if __name__ == "__main__":
    main()
