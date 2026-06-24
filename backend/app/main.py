"""FastAPI 应用入口模块。

负责加载配置、初始化日志和数据库，并注册 API 路由。
"""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.logs import router as logs_router
from app.api.media_sources import router as media_sources_router
from app.api.rename_previews import router as rename_previews_router
from app.api.scan_jobs import router as scan_jobs_router
from app.core.config import AppSettings
from app.core.config import load_settings
from app.core.database import ensure_database
from app.core.logger import get_logger, setup_logging

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

    app = FastAPI(title=settings.app_name, version=settings.version)
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(media_sources_router)
    app.include_router(scan_jobs_router)
    app.include_router(rename_previews_router)
    app.include_router(logs_router)
    logger.info("FastAPI 应用创建完成")
    return app


app = create_app()
