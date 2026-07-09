"""FastAPI 应用入口模块。

负责加载配置、初始化日志和数据库，并注册 API 路由。
"""

from fastapi import FastAPI

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.external_submission_blocks import router as external_submission_blocks_router
from app.api.logs import router as logs_router
from app.api.media_sources import router as media_sources_router
from app.api.operation_logs import router as operation_logs_router
from app.api.pending_files import router as pending_files_router
from app.api.rename_operations import router as rename_operations_router
from app.api.rename_previews import router as rename_previews_router
from app.api.rename_rollback_plans import router as rename_rollback_plans_router
from app.api.scan_jobs import router as scan_jobs_router
from app.api.settings import router as settings_router
from app.api.shared_protocols import router as shared_protocols_router
from app.api.tasks import router as tasks_router
from app.api.users import router as users_router
from app.core.config import AppSettings
from app.core.config import load_settings
from app.core.database import ensure_database
from app.core.logger import get_logger, setup_logging
from app.service.auth_service import ensure_default_admin
from app.service.log_service import cleanup_logs
from app.service.operation_log_service import cleanup_operation_logs
from app.service.scan_service import recover_interrupted_scan_jobs

logger = get_logger(__name__)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """创建 FastAPI 应用实例。

    Returns:
        完成基础设施初始化和路由注册的 FastAPI 应用。
    """

    if settings is None:
        settings = load_settings()
    setup_logging(settings.logging)
    logger.info("开始创建 FastAPI 应用")
    ensure_database(settings)
    default_admin = ensure_default_admin(settings)
    if default_admin is not None:
        logger.warning("已创建默认管理员账号，请登录后立即修改密码")
    cleanup_logs(settings)
    cleanup_operation_logs(settings)
    recovered_scan_jobs = recover_interrupted_scan_jobs(settings)
    if recovered_scan_jobs:
        logger.warning("已恢复 %s 个未完成扫描任务", recovered_scan_jobs)

    app = FastAPI(title=settings.app_name, version=settings.version)
    app.state.settings = settings
    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(users_router)
    app.include_router(health_router)
    app.include_router(media_sources_router)
    app.include_router(shared_protocols_router)
    app.include_router(scan_jobs_router)
    app.include_router(rename_previews_router)
    app.include_router(rename_operations_router)
    app.include_router(rename_rollback_plans_router)
    app.include_router(pending_files_router)
    app.include_router(logs_router)
    app.include_router(operation_logs_router)
    app.include_router(tasks_router)
    app.include_router(settings_router)
    app.include_router(external_submission_blocks_router)
    logger.info("FastAPI 应用创建完成")
    return app


app = create_app()
