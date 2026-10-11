"""重命名队列的隐藏进程与生命周期监督。"""

from contextlib import closing
import sqlite3

from app.service.isolated_task_service import TaskProcessHooks, runTaskProcessLoop, startTaskProcess
from app.service.rename_queue_service import (
    claimRename, finishInterruptedRename, heartbeatRename, recoverExpiredRenames, RENAME_TASK_TIMEOUT_SECONDS,
)


def startRenameProcess(settings, operationId, token):
    def persistPid(pid):
        with closing(sqlite3.connect(settings.database_path)) as connection:
            changed = connection.execute("UPDATE rename_operations SET execution_pid=? WHERE id=? "
                "AND status='running' AND execution_token=?", (pid, operationId, token)).rowcount
            connection.commit()
            if changed != 1:
                raise ValueError("重命名执行授权已失效")
    return startTaskProcess(settings, "app.rename_worker", {"operation_id": operationId, "token": token}, persistPid)


async def runRenameWorkerLoop(settings, stop):
    await runTaskProcessLoop(settings, stop, TaskProcessHooks("重命名", recoverExpiredRenames, claimRename,
        startRenameProcess, lambda *_: RENAME_TASK_TIMEOUT_SECONDS, heartbeatRename, finishInterruptedRename))
