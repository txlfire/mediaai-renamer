"""日志查看和导出 API。"""

from fastapi import APIRouter, Depends, Request, Response
from app.api.auth import require_permission

from app.service.log_service import cleanup_logs, export_logs_text, read_log_page, getLogStorageStats

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", dependencies=[Depends(require_permission("audit:read"))])
def get_logs(request: Request):
    """查询日志条目。"""

    return read_log_page(request.app.state.settings)


@router.get("/export", dependencies=[Depends(require_permission("audit:read"))])
def export_logs(request: Request) -> Response:
    """导出日志文本。"""

    return Response(
        content=export_logs_text(request.app.state.settings),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/storage", dependencies=[Depends(require_permission("audit:read"))])
def get_log_storage(request: Request):
    """区分日志内容量、文件长度、WAL与数据库可复用页；不触发压缩或删除。"""
    return getLogStorageStats(request.app.state.settings)


@router.post("/cleanup", dependencies=[Depends(require_permission("settings:write"))])
def cleanup_log_files(request: Request):
    """按配置和单轮预算清理、归档日志文件。"""

    return cleanup_logs(request.app.state.settings)
