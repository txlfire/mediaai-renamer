"""FastAPI 应用入口模块。

负责加载配置、初始化日志和数据库，并注册 API 路由。
"""

from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.auth import require_authenticated_user
from app.api.health import router as health_router
from app.api.external_submission_blocks import router as external_submission_blocks_router
from app.api.logs import router as logs_router
from app.api.media_sources import router as media_sources_router
from app.api.operation_logs import router as operation_logs_router
from app.api.pending_files import router as pending_files_router
from app.api.rename_operations import router as rename_operations_router
from app.api.rename_previews import router as rename_previews_router
from app.api.rename_rollback_plans import router as rename_rollback_plans_router
from app.api.remote_operations import router as remote_operations_router
from app.api.scan_jobs import router as scan_jobs_router
from app.api.settings import router as settings_router
from app.api.shared_protocols import router as shared_protocols_router
from app.api.tasks import router as tasks_router
from app.api.users import router as users_router
from app.core.config import AppSettings
from app.core.config import load_settings
from app.core.database import ensure_database
from app.core.logger import get_logger, setup_logging, shutdown_logging
from app.service.auth_service import ensure_default_admin
from app.service.log_maintenance_service import runMaintenanceLoop
from app.service.scan_worker_service import runScanWorkerLoop
from app.service.rename_worker_service import runRenameWorkerLoop
from app.service.rollback_worker_service import runRollbackWorkerLoop
from app.service.pending_move_worker_service import runPendingMoveWorkerLoop
from app.service.scan_service import recover_interrupted_scan_jobs
from app.service.settings_service import resolveRuntimeLogging
from app.web import mount_frontend

logger = get_logger(__name__)


def _httpDetail(detail: object) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail
    return str(detail) if detail is not None else "请求处理失败"


async def _validationExceptionHandler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.get("loc", ()) if part not in {"query", "body", "path"})
        message = str(first.get("msg") or "参数无效")
        detail = f"请求参数校验失败：{location + ' ' if location else ''}{message}"
    else:
        detail = "请求参数校验失败"
    return JSONResponse(status_code=422, content={"detail": detail})


async def _httpExceptionHandler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": _httpDetail(exc.detail)}, headers=exc.headers)


async def _unexpectedExceptionHandler(request: Request, exc: Exception) -> JSONResponse:
    correlationId = uuid4().hex
    logger.error(
        "未预期请求异常 correlation_id=%s method=%s path=%s error_type=%s",
        correlationId,
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误，关联ID：{correlationId}"},
        headers={"X-Correlation-ID": correlationId},
    )


def create_app(
    settings: AppSettings | None = None,
    *,
    frontend_dir: Path | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例。

    Returns:
        完成基础设施初始化和路由注册的 FastAPI 应用。
    """

    if settings is None:
        settings = load_settings()
    setup_logging(settings.logging)
    logger.info("开始创建 FastAPI 应用")
    ensure_database(settings)
    setup_logging(resolveRuntimeLogging(settings))
    default_admin = ensure_default_admin(settings)
    if default_admin is not None:
        logger.warning("已创建默认管理员账号，请登录后立即修改密码")
    recovered_scan_jobs = recover_interrupted_scan_jobs(settings)
    if recovered_scan_jobs:
        logger.warning("已将 %s 个无执行租约的遗留扫描任务标记失败；未重新执行", recovered_scan_jobs)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = asyncio.Event()
        maintenance = asyncio.create_task(runMaintenanceLoop(settings, stop))
        scans = asyncio.create_task(runScanWorkerLoop(settings, stop))
        renames = asyncio.create_task(runRenameWorkerLoop(settings, stop))
        rollbacks = asyncio.create_task(runRollbackWorkerLoop(settings, stop))
        pendingMoves = asyncio.create_task(runPendingMoveWorkerLoop(settings, stop))
        try:
            yield
        finally:
            stop.set()
            # 等待本轮维护结束，不能取消线程后假称文件/数据库工作已停止。
            await asyncio.gather(maintenance, scans, renames, rollbacks, pendingMoves)
            shutdown_logging()

    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.add_exception_handler(RequestValidationError, _validationExceptionHandler)
    app.add_exception_handler(StarletteHTTPException, _httpExceptionHandler)
    app.add_exception_handler(Exception, _unexpectedExceptionHandler)
    app.include_router(auth_router)
    private_dependencies = [Depends(require_authenticated_user())]
    app.include_router(audit_router, dependencies=private_dependencies)
    app.include_router(users_router, dependencies=private_dependencies)
    app.include_router(health_router)
    app.include_router(media_sources_router, dependencies=private_dependencies)
    app.include_router(shared_protocols_router, dependencies=private_dependencies)
    app.include_router(scan_jobs_router, dependencies=private_dependencies)
    app.include_router(rename_previews_router, dependencies=private_dependencies)
    app.include_router(rename_operations_router, dependencies=private_dependencies)
    app.include_router(rename_rollback_plans_router, dependencies=private_dependencies)
    app.include_router(remote_operations_router, dependencies=private_dependencies)
    app.include_router(pending_files_router, dependencies=private_dependencies)
    app.include_router(logs_router, dependencies=private_dependencies)
    app.include_router(operation_logs_router, dependencies=private_dependencies)
    app.include_router(tasks_router, dependencies=private_dependencies)
    app.include_router(settings_router, dependencies=private_dependencies)
    app.include_router(external_submission_blocks_router, dependencies=private_dependencies)
    frontend_mounted = mount_frontend(app, frontend_dir)
    logger.info("前端静态资源%s挂载", "已" if frontend_mounted else "未")
    logger.info("FastAPI 应用创建完成")
    return app


app = create_app()
