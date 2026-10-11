"""扫描隔离子进程入口；不导入app.main，不初始化数据库或管理员。"""

import json
from pathlib import Path
import sys

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.config import externalRequestSettingsFromDict
from app.core.logger import setup_logging, shutdown_logging
from app.service.scan_queue_service import executeAcceptedScan, finishInterruptedScan


def main() -> int:
    payload = json.loads(sys.stdin.buffer.readline(65537))
    settings = AppSettings(database_path=Path(payload["database_path"]), data_dir=Path(payload["data_dir"]),
        external_requests=externalRequestSettingsFromDict(payload.get("external_requests", {})),
        credential_key_file=Path(payload["credential_key_file"]) if payload.get("credential_key_file") else None,
        logging=LoggingSettings(log_dir=Path(payload["log_dir"]), level=payload["log_level"],
            max_size_mb=payload["log_max_size_mb"], backup_count=payload["log_backup_count"], console_output=False),
        scan=ScanSettings(batch_size=payload["batch_size"], batch_interval_seconds=payload["batch_interval_seconds"]))
    setup_logging(settings.logging)
    try:
        executeAcceptedScan(settings, payload["job_id"], payload["token"])
        return 0
    except Exception as exc:  # noqa: BLE001 - 子进程错误不向标准输出泄露配置或路径。
        finishInterruptedScan(settings, payload["job_id"], payload["token"],
                              f"后台扫描执行失败（{type(exc).__name__}），请查看任务日志", processExited=False)
        return 1
    finally:
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
