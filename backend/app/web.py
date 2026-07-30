"""前端静态资源托管模块。"""

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SpaStaticFiles(StaticFiles):
    """提供静态文件，并为 Vue 前端路由回退到首页。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """返回静态资源；无扩展名路径缺失时返回 index.html。"""

        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)

        if response.status_code == 404 and not Path(path).suffix:
            return await super().get_response("index.html", scope)
        return response


def resolve_frontend_dir(frontend_dir: Path | None = None) -> Path:
    """解析构建后的前端静态资源目录。"""

    if frontend_dir is not None:
        return frontend_dir
    return Path(os.getenv("MEDIAAI_FRONTEND_DIR", "/app/frontend-dist"))


def mount_frontend(app: FastAPI, frontend_dir: Path | None = None) -> bool:
    """在前端构建产物存在时挂载静态资源。"""

    directory = resolve_frontend_dir(frontend_dir)
    if not (directory / "index.html").is_file():
        return False
    app.mount(
        "/",
        SpaStaticFiles(directory=directory, html=True),
        name="frontend",
    )
    return True
