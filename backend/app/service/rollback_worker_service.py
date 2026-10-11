"""回滚队列的隐藏进程与生命周期监督。"""

from contextlib import closing
import sqlite3

from app.service.isolated_task_service import TaskProcessHooks, runTaskProcessLoop, startTaskProcess
from app.service.rollback_queue_service import (
    claimRollback, finishInterruptedRollback, heartbeatRollback, recoverExpiredRollbacks, ROLLBACK_TASK_TIMEOUT_SECONDS,
)


def startRollbackProcess(settings, planId, token):
    def persistPid(pid):
        with closing(sqlite3.connect(settings.database_path)) as connection:
            changed = connection.execute("UPDATE rename_rollback_plans SET execution_pid=? WHERE id=? "
                "AND status='running' AND execution_token=?", (pid, planId, token)).rowcount
            connection.commit()
            if changed != 1:
                raise ValueError("回滚执行授权已失效")
    return startTaskProcess(settings, "app.rollback_worker", {"plan_id": planId, "token": token}, persistPid)


async def runRollbackWorkerLoop(settings, stop):
    await runTaskProcessLoop(settings, stop, TaskProcessHooks("回滚", recoverExpiredRollbacks, claimRollback,
        startRollbackProcess, lambda *_: ROLLBACK_TASK_TIMEOUT_SECONDS, heartbeatRollback, finishInterruptedRollback))
