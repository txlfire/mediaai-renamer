"""同步扫描及干跑探测占用有界只读槽，避免目录写绕过旧路径读取。"""

from contextlib import contextmanager
from functools import wraps
import os
import time
from uuid import uuid4

from app.service.cloud_directory_guard import requireNoDirectoryExecution
from app.service.cloud_drive_budget_service import cloudDriveHostKey
from app.service.cloud_inventory_service import inventoryDb
from app.service.cloud_inventory_service import inventoryId
from app.service.scan_recovery_service import isProcessAlive
from app.service.audit_service import record_audit_event


@contextmanager
def directoryProbeWindow(settings, kind):
    if kind not in {"scan", "rename_dry_run", "rollback_dry_run"}:
        raise ValueError("不支持的同步探测类型")
    leaseId = str(uuid4())
    tracked = False
    with inventoryDb(settings, write=True) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_directory_probe_leases'").fetchone()
        if exists:
            requireNoDirectoryExecution(connection)
            if connection.execute("SELECT COUNT(*) FROM cloud_directory_probe_leases").fetchone()[0] >= 64:
                raise ValueError("本地探测任务已达并发上限，请稍后核对")
            connection.execute("INSERT INTO cloud_directory_probe_leases(id,kind,owner_pid,owner_host,created_at) VALUES(?,?,?,?,?)",
                               (leaseId, kind, os.getpid(), cloudDriveHostKey(), time.time()))
            tracked = True
    try:
        yield
    finally:
        if tracked:
            with inventoryDb(settings, write=True) as connection:
                connection.execute("DELETE FROM cloud_directory_probe_leases WHERE id=? AND owner_pid=? AND owner_host=?",
                                   (leaseId, os.getpid(), cloudDriveHostKey()))


def directoryProbe(kind):
    def decorate(function):
        @wraps(function)
        def run(settings, *args, **kwargs):
            with directoryProbeWindow(settings, kind):
                return function(settings, *args, **kwargs)
        return run
    return decorate


def inspectDirectoryProbes(settings):
    with inventoryDb(settings) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_directory_probe_leases'").fetchone()
        return [dict(row) for row in connection.execute("SELECT * FROM cloud_directory_probe_leases ORDER BY created_at,id LIMIT 64")] if exists else []


def releaseDirectoryProbeAfterReview(settings, leaseId, *, confirmProcessStopped, confirmRemoteIdle):
    inventoryId(leaseId)
    if confirmProcessStopped is not True or confirmRemoteIdle is not True:
        raise ValueError("须确认本机探测进程已停止及相关远端已静止，不自动确认")
    with inventoryDb(settings, write=True) as connection:
        row = connection.execute("SELECT * FROM cloud_directory_probe_leases WHERE id=?", (leaseId,)).fetchone()
        if row is None:
            raise ValueError("探测槽不存在")
        if (row["owner_host"] != cloudDriveHostKey() or type(row["owner_pid"]) is not int
                or row["owner_pid"] < 1 or isProcessAlive(row["owner_pid"])):
            raise ValueError("探测进程退出未确认或不属于本机，保留槽位")
        connection.execute("DELETE FROM cloud_directory_probe_leases WHERE id=?", (leaseId,))
        record_audit_event(settings, event_type="cloud_directory.probe", action="release_after_review", result="success",
            summary="本机核对并释放只读探测槽；不重放请求", target_type="cloud_directory_probe", target_id=leaseId,
            actor_name="local_directory_probe_recovery", detail={"processStoppedConfirmed": True, "remoteIdleConfirmed": True,
                "originalPid": row["owner_pid"], "replayed": False}, connection=connection)
