"""盘点子进程入口；不导入app.main，不初始化数据库，不打印交接秘密。"""

import json
from pathlib import Path
import sys

from app.core.config import AppSettings, LoggingSettings
from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings
from app.core.logger import setup_logging, shutdown_logging, get_logger
from app.service.cloud_drive_budget_service import createBudgetedCd2Client
from app.service.cloud_inventory_service import (
    inventoryDb, inventoryId, _active, executeCloudInventoryQuantum, safeCloudInventoryReason, pauseCloudInventoryExecution,
)


def main():
    settings = None
    jobId = workerToken = None
    line = sys.stdin.buffer.readline(65537)
    if len(line) > 65536 or not line.endswith(b"\n"):
        return 1
    try:
        payload = json.loads(line)
        settings = AppSettings(database_path=Path(payload["database_path"]), data_dir=Path(payload["data_dir"]),
            logging=LoggingSettings(log_dir=Path(payload["log_dir"]), level=payload["log_level"],
                max_size_mb=payload["log_max_size_mb"], backup_count=payload["log_backup_count"], console_output=False))
        setup_logging(settings.logging)
        jobId, workerToken = inventoryId(payload["job_id"]), inventoryId(payload["worker_token"])
        revision = payload["configuration_revision"]
        if type(revision) is not int or revision < 0:
            raise ValueError("配置修订无效")
        settings = resolveCloudDriveSettings(settings, expectedRevision=revision)
        with inventoryDb(settings) as connection:
            job = _active(connection, jobId, workerToken)
            sessionId, root = job["session_id"], job["root_path"]
        ca = settings.cloudDrive.caCertificatePem.encode("ascii") or None
        get_logger(__name__).info("CD2盘点量子开始 job_id=%s", job["id"])
        with createBudgetedCd2Client(settings, sessionId, endpoint=settings.cloudDrive.endpoint, token=payload["api_token"],
                                    rootPath=root, trustedCaPem=ca, timeoutSeconds=60) as client:
            executeCloudInventoryQuantum(settings, job["id"], payload["worker_token"], client)
        get_logger(__name__).info("CD2盘点量子结束 job_id=%s", job["id"])
        return 0
    except Exception as exc:  # 不记录原始异常、payload或远端说明。
        code = getattr(exc, "code", None)
        safeCode = safeCloudInventoryReason(code)
        logger = get_logger(__name__)
        if settings is not None and jobId is not None and workerToken is not None:
            try:
                # 启动失败也保留固定原因；函数仍核对PID和worker令牌，不借此释放RPC槽。
                pauseCloudInventoryExecution(settings, jobId, workerToken, safeCode)
            except Exception as stateError:
                logger.error("CD2盘点停止原因保存失败 job_id=%s error_type=%s code=%s",
                    jobId, type(stateError).__name__, safeCloudInventoryReason(getattr(stateError, "code", None)))
        logger.error("CD2盘点子进程停止 job_id=%s error_type=%s code=%s", jobId or "unknown", type(exc).__name__, safeCode)
        return 1
    finally:
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
