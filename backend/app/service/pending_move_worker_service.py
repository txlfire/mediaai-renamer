"""迁移隐藏进程复用既有监督器及共同文件写槽。"""

from contextlib import closing
import sqlite3

from app.service.isolated_task_service import TaskProcessHooks, runTaskProcessLoop, startTaskProcess
from app.service.pending_move_service import (
    claimPendingMove, finishInterruptedPendingMove, heartbeatPendingMove, recoverExpiredPendingMoves,
)
from app.service.file_task_queue_service import FILE_TASK_TIMEOUT_SECONDS


def startPendingMoveProcess(settings, taskId, token):
    def persistPid(pid):
        with closing(sqlite3.connect(settings.database_path)) as connection:
            changed = connection.execute("UPDATE pending_move_tasks SET execution_pid=? WHERE id=? AND status='running' AND execution_token=?",
                                         (pid, taskId, token)).rowcount
            connection.commit()
            if changed != 1:
                raise ValueError("迁移执行授权已失效")
    return startTaskProcess(settings, "app.pending_move_worker", {"task_id": taskId, "token": token}, persistPid)


async def runPendingMoveWorkerLoop(settings, stop):
    await runTaskProcessLoop(settings, stop, TaskProcessHooks("迁移", recoverExpiredPendingMoves, claimPendingMove,
        startPendingMoveProcess, lambda *_: FILE_TASK_TIMEOUT_SECONDS, heartbeatPendingMove, finishInterruptedPendingMove))
