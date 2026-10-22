"""只读目录位置核对子进程，不加载应用、不初始化库、秘密仅stdin。"""

import json
from pathlib import Path
import sys

from app.core.config import AppSettings, LoggingSettings
from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings
from app.core.logger import setup_logging, shutdown_logging, get_logger
from app.service.cloud_inventory_service import inventoryId
from app.service.cloud_directory_recovery_worker_service import executeDirectoryRecovery, stopDirectoryRecovery
from app.service.cloud_directory_worker_service import safeDirectoryReason


def main():
    settings, recoveryId, token, payload = None, None, None, {}
    line = sys.stdin.buffer.readline(65537)
    if len(line) > 65536 or not line.endswith(b"\n"):
        return 1
    try:
        payload = json.loads(line)
        line = b""
        settings = AppSettings(database_path=Path(payload["database_path"]), data_dir=Path(payload["data_dir"]),
            logging=LoggingSettings(log_dir=Path(payload["log_dir"]),
                level=payload["log_level"], max_size_mb=payload["log_max_size_mb"], backup_count=payload["log_backup_count"], console_output=False))
        setup_logging(settings.logging)
        recoveryId, token = inventoryId(payload["recovery_id"]), inventoryId(payload["worker_token"])
        revision = payload["configuration_revision"]
        if type(revision) is not int or revision < 0:
            raise ValueError("配置修订无效")
        settings = resolveCloudDriveSettings(settings, expectedRevision=revision)
        executeDirectoryRecovery(settings, recoveryId, token, payload.pop("api_token"))
        return 0
    except Exception as exc:
        reason = safeDirectoryReason(getattr(exc, "code", None))
        if settings is not None and recoveryId is not None and token is not None:
            try:
                stopDirectoryRecovery(settings, recoveryId, token, reason)
            except Exception as stateError:
                get_logger(__name__).error("C2只读恢复状态保存失败 recovery_id=%s error_type=%s", recoveryId, type(stateError).__name__)
        get_logger(__name__).error("C2只读恢复停止 recovery_id=%s error_type=%s code=%s", recoveryId or "unknown", type(exc).__name__, reason)
        return 1
    finally:
        payload.pop("api_token", None)
        line = b""
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
