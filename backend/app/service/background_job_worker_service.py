"""后台探测/外发独立槽位；不与真实文件写队列混用。"""

from contextlib import closing
from dataclasses import asdict
import sqlite3

from app.service.background_job_service import (
    claimBackgroundJob, heartbeatBackgroundJob, recoverExpiredBackgroundJobs, finishInterruptedBackgroundJob,
)
from app.service.isolated_task_service import TaskProcessHooks, startTaskProcess, runTaskProcessLoop


def startBackgroundJobProcess(settings, jobId, token):
    def persistPid(pid):
        with closing(sqlite3.connect(settings.database_path)) as connection:
            changed = connection.execute("UPDATE background_jobs SET execution_pid=? WHERE id=? AND status='running' "
                "AND execution_token=?", (pid, jobId, token)).rowcount
            connection.commit()
            if changed != 1:
                raise ValueError("后台任务授权已失效")
    return startTaskProcess(settings, "app.background_job_worker", {
        "job_id": jobId, "token": token, "background_jobs": asdict(settings.background_jobs),
    }, persistPid)


async def runBackgroundJobLoop(settings, stop):
    await runTaskProcessLoop(settings, stop, TaskProcessHooks("后台探测/外部匹配", recoverExpiredBackgroundJobs,
        claimBackgroundJob, startBackgroundJobProcess, lambda *_: settings.background_jobs.timeout_seconds,
        heartbeatBackgroundJob, finishInterruptedBackgroundJob))
