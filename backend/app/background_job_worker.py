"""后台任务子进程入口，不初始化数据库或管理员，不导入app.main。"""

import json
from pathlib import Path
import sys

from app.core.config import AppSettings, LoggingSettings, externalRequestSettingsFromDict, backgroundJobSettingsFromDict
from app.core.logger import setup_logging, shutdown_logging
from app.service.background_job_service import executeBackgroundJob, finishInterruptedBackgroundJob, BackgroundJobInterrupted


def main():
    payload = json.loads(sys.stdin.buffer.readline(65537))
    settings = AppSettings(database_path=Path(payload["database_path"]), data_dir=Path(payload["data_dir"]),
        credential_key_file=Path(payload["credential_key_file"]) if payload.get("credential_key_file") else None,
        external_requests=externalRequestSettingsFromDict(payload.get("external_requests", {})),
        background_jobs=backgroundJobSettingsFromDict(payload.get("background_jobs", {})),
        logging=LoggingSettings(log_dir=Path(payload["log_dir"]), level=payload["log_level"],
            max_size_mb=payload["log_max_size_mb"], backup_count=payload["log_backup_count"], console_output=False))
    setup_logging(settings.logging)
    try:
        executeBackgroundJob(settings, payload["job_id"], payload["token"])
        return 0
    except Exception as exc:  # 不写原始供应商/网络异常或凭据，未知请求不重放。
        reason = str(exc) if isinstance(exc, BackgroundJobInterrupted) else f"后台任务执行失败（{type(exc).__name__}）"
        finishInterruptedBackgroundJob(settings, payload["job_id"], payload["token"], reason, processExited=False)
        return 1
    finally:
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
