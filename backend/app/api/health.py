"""健康检查接口模块。

用于前端和部署平台确认后端服务是否已经启动并可访问。
"""

from fastapi import APIRouter

from app.core.logger import get_logger

router = APIRouter(prefix="/api", tags=["health"])
logger = get_logger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    """返回后端健康状态。

    Returns:
        应用名称、版本号和运行状态。
    """

    logger.debug("收到健康检查请求")
    return {
        "app": "MediaAI Renamer",
        "version": "0.1.0",
        "status": "ok",
    }
