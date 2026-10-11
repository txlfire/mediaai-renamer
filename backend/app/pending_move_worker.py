"""迁移隔离子进程，不导入main或初始化实例。"""

import json
from pathlib import Path
import sys

from app.core.config import AppSettings, LoggingSettings, externalRequestSettingsFromDict
from app.core.logger import setup_logging, shutdown_logging
from app.service.pending_move_service import executeAcceptedPendingMove, finishInterruptedPendingMove


def main():
    payload = json.loads(sys.stdin.buffer.readline(65537))
    settings = AppSettings(database_path=Path(payload["database_path"]), data_dir=Path(payload["data_dir"]),
        external_requests=externalRequestSettingsFromDict(payload.get("external_requests", {})),
        credential_key_file=Path(payload["credential_key_file"]) if payload.get("credential_key_file") else None,
        logging=LoggingSettings(log_dir=Path(payload["log_dir"]), level=payload["log_level"],
            max_size_mb=payload["log_max_size_mb"], backup_count=payload["log_backup_count"], console_output=False))
    setup_logging(settings.logging)
    try:
        executeAcceptedPendingMove(settings, payload["task_id"], payload["token"])
        return 0
    except Exception as exc:  # noqa: BLE001 - 保留意图，隐藏输出不泄露路径或凭据。
        finishInterruptedPendingMove(settings, payload["task_id"], payload["token"],
            f"迁移执行中断（{type(exc).__name__}），请核对任务和文件动作", processExited=False)
        return 1
    finally:
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
