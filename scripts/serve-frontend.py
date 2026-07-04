"""Serve built frontend files and proxy API requests to backend."""

from __future__ import annotations

import argparse
import http.server
import mimetypes
import pathlib
import socketserver
import sys
import urllib.error
import urllib.request


class FrontendProxyHandler(http.server.SimpleHTTPRequestHandler):
    """静态托管前端构建产物，并将 /api 请求代理到后端。"""

    backend_url: str = "http://127.0.0.1:8970"

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy_request()
            return
        self._serve_frontend()

    def do_POST(self) -> None:
        self._proxy_request()

    def do_PUT(self) -> None:
        self._proxy_request()

    def do_DELETE(self) -> None:
        self._proxy_request()

    def _serve_frontend(self) -> None:
        requested = self.translate_path(self.path)
        if not pathlib.Path(requested).exists():
            self.path = "/index.html"
        super().do_GET()

    def _proxy_request(self) -> None:
        target = f"{self.backend_url}{self.path}"
        body = None
        if "Content-Length" in self.headers:
            body = self.rfile.read(int(self.headers["Content-Length"]))

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        request = urllib.request.Request(
            target,
            data=body,
            headers=headers,
            method=self.command,
        )

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() not in {"transfer-encoding", "connection"}:
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(response.read())
        except urllib.error.HTTPError as exc:
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                if key.lower() not in {"transfer-encoding", "connection"}:
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(exc.read())
        except Exception as exc:  # noqa: BLE001
            payload = f"Backend proxy failed: {exc}".encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--backend", default="http://127.0.0.1:8970")
    parser.add_argument("--dist", default="frontend/dist")
    args = parser.parse_args()

    dist_dir = pathlib.Path(args.dist).resolve()
    if not (dist_dir / "index.html").exists():
        raise SystemExit(f"frontend dist not found: {dist_dir}")

    log_dir = pathlib.Path(".codex/run-logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = (log_dir / "frontend-vite.out.log").open("a", encoding="utf-8", buffering=1)
    sys.stderr = (log_dir / "frontend-vite.err.log").open("a", encoding="utf-8", buffering=1)

    mimetypes.add_type("application/javascript", ".js")
    FrontendProxyHandler.backend_url = args.backend.rstrip("/")

    handler = lambda *handler_args, **handler_kwargs: FrontendProxyHandler(  # noqa: E731
        *handler_args,
        directory=str(dist_dir),
        **handler_kwargs,
    )
    with socketserver.ThreadingTCPServer((args.host, args.port), handler) as server:
        server.daemon_threads = True
        print(f"Frontend server listening on http://{args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
