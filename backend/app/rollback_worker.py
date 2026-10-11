"""回滚子进程入口，不初始化数据库/管理员，也不导入app.main。"""

import json
from pathlib import Path
import sys

from app.core.config import AppSettings, LoggingSettings
from app.core.config import externalRequestSettingsFromDict
from app.core.logger import setup_logging, shutdown_logging
from app.service.rollback_queue_service import executeAcceptedRollback, finishInterruptedRollback, RollbackLeaseError


def main():
    payload = json.loads(sys.stdin.buffer.readline(65537))
    settings = AppSettings(database_path=Path(payload["database_path"]), data_dir=Path(payload["data_dir"]),
        external_requests=externalRequestSettingsFromDict(payload.get("external_requests", {})),
        credential_key_file=Path(payload["credential_key_file"]) if payload.get("credential_key_file") else None,
        logging=LoggingSettings(log_dir=Path(payload["log_dir"]), level=payload["log_level"],
            max_size_mb=payload["log_max_size_mb"], backup_count=payload["log_backup_count"], console_output=False))
    setup_logging(settings.logging)
    try:
        executeAcceptedRollback(settings, payload["plan_id"], payload["token"])
        return 0
    except Exception as exc:  # noqa: BLE001 - 未知文件动作留待核对，不输出路径及凭据。
        finishInterruptedRollback(settings, payload["plan_id"], payload["token"],
            str(exc) if isinstance(exc, RollbackLeaseError) else
            f"后台回滚执行失败（{type(exc).__name__}），请核对任务及文件动作", processExited=False)
        return 1
    finally:
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
