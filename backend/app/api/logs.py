"""日志查看和导出 API。"""

from fastapi import APIRouter, Request, Response

from app.service.log_service import cleanup_logs, export_logs_text, read_log_items

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def get_logs(request: Request):
    """查询日志条目。"""

    return {"items": read_log_items(request.app.state.settings)}


@router.get("/export")
def export_logs(request: Request) -> Response:
    """导出日志文本。"""

    return Response(
        content=export_logs_text(request.app.state.settings),
        media_type="text/plain; charset=utf-8",
    )


@router.post("/cleanup")
def cleanup_log_files(request: Request):
    """Clean and archive log files."""

    return cleanup_logs(request.app.state.settings)
