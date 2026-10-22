"""目录动作子进程：不导入app.main、不初始化数据库、不打印交接秘密。"""

import json
from pathlib import Path
import sys

from app.core.config import AppSettings, LoggingSettings
from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings
from app.core.logger import setup_logging, shutdown_logging, get_logger
from app.service.cloud_directory_worker_service import executeDirectoryBatch, stopDirectoryWorker, safeDirectoryReason
from app.service.cloud_inventory_service import inventoryId


def main():
    settings = None
    planId = token = None
    payload = {}
    line = sys.stdin.buffer.readline(65537)
    if len(line) > 65536 or not line.endswith(b"\n"):
        return 1
    try:
        payload = json.loads(line)
        line = b""
        settings = AppSettings(database_path=Path(payload["database_path"]), data_dir=Path(payload["data_dir"]),
            logging=LoggingSettings(
                log_dir=Path(payload["log_dir"]), level=payload["log_level"], max_size_mb=payload["log_max_size_mb"],
                backup_count=payload["log_backup_count"], console_output=False))
        setup_logging(settings.logging)
        planId, token = inventoryId(payload["plan_id"]), inventoryId(payload["worker_token"])
        revision = payload["configuration_revision"]
        if type(revision) is not int or revision < 0:
            raise ValueError("配置修订无效")
        settings = resolveCloudDriveSettings(settings, expectedRevision=revision)
        executeDirectoryBatch(settings, planId, token, payload.pop("api_token"))
        return 0
    except Exception as exc:
        code = safeDirectoryReason(getattr(exc, "code", None))
        if settings is not None and planId is not None and token is not None:
            try:
                stopDirectoryWorker(settings, planId, token, code)
            except Exception as stateError:
                get_logger(__name__).error("C2停止状态保存失败 plan_id=%s error_type=%s", planId, type(stateError).__name__)
        get_logger(__name__).error("C2子进程停止 plan_id=%s error_type=%s code=%s", planId or "unknown", type(exc).__name__, code)
        return 1
    finally:
        payload.pop("api_token", None)
        line = b""
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
