"""远程文件操作锁和幂等记录服务。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3
from typing import Any

from app.core.config import AppSettings


class RemoteOperationLockError(RuntimeError):
    """远程操作锁通用错误。"""


class RemoteOperationLockConflict(RemoteOperationLockError):
    """远程操作锁被其他有效租约占用。"""


@dataclass(frozen=True)
class RemoteOperationLock:
    """远程媒体源写操作租约。"""

    id: int
    media_source_id: int
    lock_key: str
    owner: str | None
    task_type: str | None
    task_id: int | None
    lease_token: str
    heartbeat_at: str
    expires_at: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RemoteOperationItem:
    """远程操作幂等明细。"""

    id: int
    media_source_id: int
    operation_type: str
    idempotency_key: str
    source_path: str
    target_path: str | None
    source_version: str | None
    target_version: str | None
    status: str
    error_message: str | None
    recovery: dict[str, Any] | list[Any] | None
    created_at: str
    updated_at: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _connect(settings: AppSettings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_lock(row: sqlite3.Row) -> RemoteOperationLock:
    return RemoteOperationLock(
        id=int(row["id"]),
        media_source_id=int(row["media_source_id"]),
        lock_key=str(row["lock_key"]),
        owner=row["owner"],
        task_type=row["task_type"],
        task_id=int(row["task_id"]) if row["task_id"] is not None else None,
        lease_token=str(row["lease_token"]),
        heartbeat_at=str(row["heartbeat_at"]),
        expires_at=str(row["expires_at"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_item(row: sqlite3.Row) -> RemoteOperationItem:
    recovery_json = row["recovery_json"]
    recovery: dict[str, Any] | list[Any] | None = None
    if recovery_json:
        parsed = json.loads(str(recovery_json))
        if isinstance(parsed, (dict, list)):
            recovery = parsed
    return RemoteOperationItem(
        id=int(row["id"]),
        media_source_id=int(row["media_source_id"]),
        operation_type=str(row["operation_type"]),
        idempotency_key=str(row["idempotency_key"]),
        source_path=str(row["source_path"]),
        target_path=row["target_path"],
        source_version=row["source_version"],
        target_version=row["target_version"],
        status=str(row["status"]),
        error_message=row["error_message"],
        recovery=recovery,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _fetch_lock(connection: sqlite3.Connection, lock_key: str) -> RemoteOperationLock | None:
    row = connection.execute(
        "SELECT * FROM remote_operation_locks WHERE lock_key = ?",
        (lock_key,),
    ).fetchone()
    return _row_to_lock(row) if row else None


def acquire_remote_operation_lock(
    settings: AppSettings,
    *,
    media_source_id: int,
    lock_key: str,
    owner: str | None = None,
    task_type: str | None = None,
    task_id: int | None = None,
    ttl_seconds: int = 300,
) -> RemoteOperationLock:
    """申请远程媒体源写操作租约。

    同一 `lock_key` 在有效租约内只能被一个任务持有；过期或已释放的租约可被覆盖。
    """

    if not lock_key.strip():
        raise ValueError("远程操作锁 key 不能为空")
    safe_ttl = max(1, int(ttl_seconds))
    now = _utc_now()
    now_text = _utc_text(now)
    expires_at = _utc_text(now + timedelta(seconds=safe_ttl))
    lease_token = secrets.token_urlsafe(24)

    with closing(_connect(settings)) as connection:
        existing = _fetch_lock(connection, lock_key)
        if existing and existing.status == "active" and _parse_time(existing.expires_at) > now:
            raise RemoteOperationLockConflict("远程媒体源正在执行写操作，请稍后重试")

        if existing:
            connection.execute(
                "UPDATE remote_operation_locks SET media_source_id = ?, owner = ?, task_type = ?, "
                "task_id = ?, lease_token = ?, heartbeat_at = ?, expires_at = ?, status = 'active', "
                "updated_at = ? WHERE lock_key = ?",
                (
                    media_source_id,
                    owner,
                    task_type,
                    task_id,
                    lease_token,
                    now_text,
                    expires_at,
                    now_text,
                    lock_key,
                ),
            )
        else:
            connection.execute(
                "INSERT INTO remote_operation_locks "
                "(media_source_id, lock_key, owner, task_type, task_id, lease_token, heartbeat_at, "
                "expires_at, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    media_source_id,
                    lock_key,
                    owner,
                    task_type,
                    task_id,
                    lease_token,
                    now_text,
                    expires_at,
                    now_text,
                    now_text,
                ),
            )
        connection.commit()
        lock = _fetch_lock(connection, lock_key)
    if lock is None:
        raise RemoteOperationLockError("远程操作锁创建失败")
    return lock


def heartbeat_remote_operation_lock(
    settings: AppSettings,
    lock_key: str,
    lease_token: str,
    *,
    ttl_seconds: int = 300,
) -> RemoteOperationLock:
    """刷新远程操作租约心跳。"""

    safe_ttl = max(1, int(ttl_seconds))
    now = _utc_now()
    now_text = _utc_text(now)
    expires_at = _utc_text(now + timedelta(seconds=safe_ttl))
    with closing(_connect(settings)) as connection:
        lock = _fetch_lock(connection, lock_key)
        if (
            lock is None
            or lock.lease_token != lease_token
            or lock.status != "active"
            or _parse_time(lock.expires_at) <= now
        ):
            raise RemoteOperationLockError("远程操作锁租约无效或已过期")
        connection.execute(
            "UPDATE remote_operation_locks SET heartbeat_at = ?, expires_at = ?, updated_at = ? "
            "WHERE lock_key = ? AND lease_token = ? AND status = 'active'",
            (now_text, expires_at, now_text, lock_key, lease_token),
        )
        connection.commit()
        updated = _fetch_lock(connection, lock_key)
    if updated is None:
        raise RemoteOperationLockError("远程操作锁刷新失败")
    return updated


def release_remote_operation_lock(settings: AppSettings, lock_key: str, lease_token: str) -> bool:
    """释放远程操作租约。"""

    now_text = _utc_text()
    with closing(_connect(settings)) as connection:
        cursor = connection.execute(
            "UPDATE remote_operation_locks SET status = 'released', updated_at = ? "
            "WHERE lock_key = ? AND lease_token = ? AND status = 'active'",
            (now_text, lock_key, lease_token),
        )
        connection.commit()
        return cursor.rowcount > 0


def create_remote_operation_item(
    settings: AppSettings,
    *,
    media_source_id: int,
    operation_type: str,
    idempotency_key: str,
    source_path: str,
    target_path: str | None = None,
    source_version: str | None = None,
    target_version: str | None = None,
    recovery: dict[str, Any] | list[Any] | None = None,
) -> RemoteOperationItem:
    """创建远程操作明细；相同幂等键重复调用时返回已有记录。"""

    if not idempotency_key.strip():
        raise ValueError("远程操作幂等键不能为空")
    if not source_path.strip():
        raise ValueError("远程操作源路径不能为空")
    now_text = _utc_text()
    recovery_json = json.dumps(recovery, ensure_ascii=False, separators=(",", ":")) if recovery is not None else None
    with closing(_connect(settings)) as connection:
        existing = connection.execute(
            "SELECT * FROM remote_operation_items WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            return _row_to_item(existing)
        cursor = connection.execute(
            "INSERT INTO remote_operation_items "
            "(media_source_id, operation_type, idempotency_key, source_path, target_path, "
            "source_version, target_version, status, error_message, recovery_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?)",
            (
                media_source_id,
                operation_type,
                idempotency_key,
                source_path,
                target_path,
                source_version,
                target_version,
                recovery_json,
                now_text,
                now_text,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _row_to_item(row)


def update_remote_operation_item_status(
    settings: AppSettings,
    item_id: int,
    status: str,
    *,
    error_message: str | None = None,
    source_version: str | None = None,
    target_version: str | None = None,
    recovery: dict[str, Any] | list[Any] | None = None,
) -> RemoteOperationItem:
    """更新远程操作明细状态。"""

    if not status.strip():
        raise ValueError("远程操作状态不能为空")
    now_text = _utc_text()
    recovery_json = json.dumps(recovery, ensure_ascii=False, separators=(",", ":")) if recovery is not None else None
    with closing(_connect(settings)) as connection:
        row = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError("远程操作明细不存在")
        next_source_version = source_version if source_version is not None else row["source_version"]
        next_target_version = target_version if target_version is not None else row["target_version"]
        next_recovery_json = recovery_json if recovery is not None else row["recovery_json"]
        connection.execute(
            "UPDATE remote_operation_items SET source_version = ?, target_version = ?, status = ?, "
            "error_message = ?, recovery_json = ?, updated_at = ? WHERE id = ?",
            (
                next_source_version,
                next_target_version,
                status,
                error_message,
                next_recovery_json,
                now_text,
                item_id,
            ),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
    return _row_to_item(updated)
