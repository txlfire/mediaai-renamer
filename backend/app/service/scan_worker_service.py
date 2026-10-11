"""扫描任务复用隔离进程监督，确认退出前不释放执行槽。"""

import asyncio
from contextlib import closing
import sqlite3
import subprocess

from app.core.config import AppSettings
from app.service.isolated_task_service import TaskProcessHooks, TaskProcessStartError, runTaskProcessLoop, startTaskProcess
from app.service.scan_queue_service import claimScan, finishInterruptedScan, heartbeatScan, recoverExpiredScans, scanTimeoutSeconds


class ScanProcessStartError(TaskProcessStartError):
    """保留扫描启动错误类型，已创建的进程必须交回监督者。"""


def startScanProcess(settings: AppSettings, jobId: int, token: str):
    def persistPid(pid):
        with closing(sqlite3.connect(settings.database_path)) as connection:
            changed = connection.execute("UPDATE scan_jobs SET execution_pid=? WHERE id=? AND execution_token=? AND status='running'",
                                         (pid, jobId, token)).rowcount
            connection.commit()
            if changed != 1:
                raise ValueError("扫描执行授权已失效")
    return startTaskProcess(settings, "app.scan_worker", {
        "job_id": jobId, "token": token,
        "batch_size": settings.scan.batch_size, "batch_interval_seconds": settings.scan.batch_interval_seconds,
    }, persistPid, ScanProcessStartError)


async def runScanWorkerLoop(settings: AppSettings, stop: asyncio.Event) -> None:
    await runTaskProcessLoop(settings, stop, TaskProcessHooks("扫描", recoverExpiredScans, claimScan,
        startScanProcess, scanTimeoutSeconds, heartbeatScan, finishInterruptedScan))
