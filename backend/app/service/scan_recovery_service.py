"""扫描遗留槽的本机核对：不终止进程、不重放扫描。"""

from contextlib import closing
import os
import sqlite3

from app.service.audit_service import record_audit_event


def isProcessAlive(pid: int) -> bool:
    if pid <= 0:
        raise ValueError("执行PID无效，无法安全确认进程退出")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                # 已退出的进程对象可能仍被父进程持有；能打开句柄不等于正在执行。
                exitCode = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(exitCode)):
                    raise ValueError("无法确认执行进程的退出状态，保留槽位")
                return exitCode.value == 259
            finally:
                kernel.CloseHandle(handle)
        error = ctypes.get_last_error()
        if error == 87:
            return False
        if error == 5:
            return True
        raise ValueError("无法确认执行进程是否存在，保留槽位")
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def inspectInterruptedScans(settings, *, limit: int = 100, afterId: int = 0) -> dict:
    if not 1 <= limit <= 1000 or afterId < 0:
        raise ValueError("核对数量或游标无效")
    with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id,status,execution_pid,execution_expires_at, "
            "execution_token IS NOT NULL AS has_execution_token, "
            "CASE WHEN status='running' THEN 'ownership_unknown' ELSE 'terminal_slot' END AS inspection_reason "
            "FROM scan_jobs WHERE (((status IN ('interrupted','completed','partial_completed','failed')) "
            "AND execution_token IS NOT NULL) OR (status='running' AND execution_pid IS NULL)) "
            "AND id>? ORDER BY id LIMIT ?",
            (afterId, limit + 1),
        ).fetchall()
    return {"items": [dict(row) for row in rows[:limit]], "has_more": len(rows) > limit,
            "next_id": rows[min(limit, len(rows)) - 1]["id"] if rows else afterId}


def releaseInterruptedScan(settings, jobId: int, *, confirmStopped: bool) -> None:
    if not confirmStopped:
        raise ValueError("释放执行槽必须显式确认服务已停止")
    if not settings.database_path.is_file():
        raise ValueError("数据库不存在，不初始化实例")
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM scan_jobs WHERE id=? "
            "AND status IN ('interrupted','completed','partial_completed','failed') "
            "AND execution_token IS NOT NULL", (jobId,)).fetchone()
        if row is None:
            raise ValueError("任务不存在或没有待核对的终态执行槽")
        if row["execution_pid"] is None or isProcessAlive(int(row["execution_pid"])):
            raise ValueError("执行进程仍存在或PID未知，拒绝释放槽位")
        # 维护只释放资源；完成、部分完成或失败的业务摘要不能被覆盖。
        connection.execute("UPDATE scan_jobs SET execution_token=NULL,execution_expires_at=NULL,execution_pid=NULL,"
                           "error_message=CASE WHEN status='interrupted' THEN "
                           "'原执行进程已退出，执行槽已释放；任务不会自动重试' ELSE error_message END WHERE id=?", (jobId,))
        record_audit_event(settings, event_type="scan.execute", action="release_scan_slot", result="success",
            summary="本机核对并释放扫描遗留槽", target_type="scan_job", target_id=jobId,
            actor_name="local_scan_recovery", detail={"process_exited": True, "replayed": False,
                                                       "scan_actor_id": row["actor_id"]}, connection=connection)
        connection.commit()
