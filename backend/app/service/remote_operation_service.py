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
    recovery_generation: int
    recovery_phase: str
    recovery_heartbeat_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RemoteOperationListItem(RemoteOperationItem):
    """远程操作列表明细。"""

    media_source_name: str | None


@dataclass(frozen=True)
class RemoteOperationPage:
    """远程操作分页结果。"""

    items: list[RemoteOperationListItem]
    total: int
    page: int
    page_size: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


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
        recovery_generation=int(row["recovery_generation"]),
        recovery_phase=str(row["recovery_phase"]),
        recovery_heartbeat_at=row["recovery_heartbeat_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_list_item(row: sqlite3.Row) -> RemoteOperationListItem:
    item = _row_to_item(row)
    return RemoteOperationListItem(
        **item.__dict__,
        media_source_name=row["media_source_name"],
    )


def _fetch_lock(connection: sqlite3.Connection, lock_key: str) -> RemoteOperationLock | None:
    row = connection.execute(
        "SELECT * FROM remote_operation_locks WHERE lock_key = ?",
        (lock_key,),
    ).fetchone()
    return _row_to_lock(row) if row else None


def _acquire_remote_operation_lock_in_transaction(
    connection: sqlite3.Connection,
    *,
    media_source_id: int,
    lock_key: str,
    owner: str | None = None,
    task_type: str | None = None,
    task_id: int | None = None,
    ttl_seconds: int = 300,
) -> RemoteOperationLock:
    if not lock_key.strip():
        raise ValueError("远程操作锁 key 不能为空")
    safe_ttl = max(1, int(ttl_seconds))
    now = _utc_now()
    now_text = _utc_text(now)
    expires_at = _utc_text(now + timedelta(seconds=safe_ttl))
    lease_token = secrets.token_urlsafe(24)

    # 条件写入同时完成竞争判断，损坏的过期时间不允许接管。
    cursor = connection.execute(
        "INSERT INTO remote_operation_locks "
        "(media_source_id, lock_key, owner, task_type, task_id, lease_token, heartbeat_at, "
        "expires_at, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?) "
        "ON CONFLICT(lock_key) DO UPDATE SET "
        "media_source_id = excluded.media_source_id, owner = excluded.owner, "
        "task_type = excluded.task_type, task_id = excluded.task_id, "
        "lease_token = excluded.lease_token, heartbeat_at = excluded.heartbeat_at, "
        "expires_at = excluded.expires_at, status = 'active', updated_at = excluded.updated_at "
        "WHERE remote_operation_locks.status <> 'active' "
        "OR julianday(remote_operation_locks.expires_at) <= julianday(?)",
        (media_source_id, lock_key, owner, task_type, task_id, lease_token,
         now_text, expires_at, now_text, now_text, now_text),
    )
    if cursor.rowcount != 1:
        raise RemoteOperationLockConflict("远程媒体源正在执行写操作，请稍后重试")
    lock = _fetch_lock(connection, lock_key)
    if lock is None:
        raise RemoteOperationLockError("远程操作锁创建失败")
    return lock


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

    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        lock = _acquire_remote_operation_lock_in_transaction(
            connection,
            media_source_id=media_source_id,
            lock_key=lock_key,
            owner=owner,
            task_type=task_type,
            task_id=task_id,
            ttl_seconds=ttl_seconds,
        )
        connection.commit()
    return lock


def claim_remote_operation_recovery(
    settings: AppSettings,
    item_id: int,
    *,
    lock_key: str,
    owner: str | None,
    allowed_statuses: set[str],
    ttl_seconds: int = 300,
) -> tuple[RemoteOperationItem, RemoteOperationLock]:
    """在同一事务内认领恢复明细和媒体源写租约。"""

    now_text = _utc_text()
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError("远程操作明细不存在")
        item = _row_to_item(row)
        if item.status not in allowed_statuses:
            raise ValueError(f"当前远程操作状态不允许恢复：{item.status}")
        lock = _acquire_remote_operation_lock_in_transaction(
            connection,
            media_source_id=item.media_source_id,
            lock_key=lock_key,
            owner=owner,
            task_type="remote_operation_recovery",
            task_id=item.id,
            ttl_seconds=ttl_seconds,
        )
        next_generation = item.recovery_generation + 1
        if item.recovery_phase in {"move_issued", "review_required"}:
            connection.execute(
                "UPDATE remote_operation_items SET recovery_generation = ?, recovery_token = NULL, "
                "recovery_phase = 'review_required', recovery_heartbeat_at = ?, "
                "status = 'recovery_required', updated_at = ? WHERE id = ?",
                (next_generation, now_text, now_text, item.id),
            )
            connection.execute(
                "UPDATE remote_operation_locks SET status = 'released', updated_at = ? "
                "WHERE lock_key = ? AND lease_token = ?",
                (now_text, lock_key, lock.lease_token),
            )
            connection.commit()
            raise RemoteOperationLockConflict("远程 MOVE 结果不明确，已停止自动重试，请人工核对")
        next_phase = "reconciling" if item.status == "remote_applied" else "probing"
        cursor = connection.execute(
            "UPDATE remote_operation_items SET recovery_generation = ?, recovery_token = ?, "
            "recovery_phase = ?, recovery_heartbeat_at = ?, status = 'recovering', "
            "error_message = NULL, updated_at = ? WHERE id = ? AND recovery_generation = ?",
            (
                next_generation,
                lock.lease_token,
                next_phase,
                now_text,
                now_text,
                item.id,
                item.recovery_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise RemoteOperationLockConflict("远程操作恢复认领已被其他任务更新")
        claimed = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (item.id,),
        ).fetchone()
        connection.commit()
    return _row_to_item(claimed), lock


def heartbeat_remote_operation_recovery(
    settings: AppSettings,
    item_id: int,
    recovery_generation: int,
    recovery_token: str,
    lock_key: str,
    *,
    ttl_seconds: int = 300,
) -> None:
    """在同一事务内刷新写租约和恢复明细心跳。"""

    safe_ttl = max(1, int(ttl_seconds))
    now = _utc_now()
    now_text = _utc_text(now)
    expires_at = _utc_text(now + timedelta(seconds=safe_ttl))
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        lock_cursor = connection.execute(
            "UPDATE remote_operation_locks SET heartbeat_at = ?, expires_at = ?, updated_at = ? "
            "WHERE lock_key = ? AND lease_token = ? AND status = 'active' "
            "AND julianday(expires_at) > julianday(?)",
            (now_text, expires_at, now_text, lock_key, recovery_token, now_text),
        )
        item_cursor = connection.execute(
            "UPDATE remote_operation_items SET recovery_heartbeat_at = ?, updated_at = ? "
            "WHERE id = ? AND recovery_generation = ? AND recovery_token = ? "
            "AND recovery_phase IN ('probing', 'move_issued', 'remote_applied', 'reconciling')",
            (now_text, now_text, item_id, recovery_generation, recovery_token),
        )
        if lock_cursor.rowcount != 1 or item_cursor.rowcount != 1:
            connection.rollback()
            raise RemoteOperationLockError("远程操作恢复租约无效、已过期或已被接管")
        connection.commit()


def update_remote_operation_recovery_state(
    settings: AppSettings,
    item_id: int,
    recovery_generation: int,
    recovery_token: str,
    *,
    status: str,
    recovery_phase: str,
    error_message: str | None = None,
    source_version: str | None = None,
    target_version: str | None = None,
    recovery: dict[str, Any] | list[Any] | None = None,
    expected_phases: set[str] | None = None,
    release_claim: bool = False,
) -> RemoteOperationItem:
    """使用 generation 和 token 对恢复状态执行 CAS 更新。"""

    if not status.strip() or not recovery_phase.strip():
        raise ValueError("远程操作恢复状态和阶段不能为空")
    now_text = _utc_text()
    recovery_json = json.dumps(recovery, ensure_ascii=False, separators=(",", ":")) if recovery is not None else None
    with closing(_connect(settings)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError("远程操作明细不存在")
        next_source_version = source_version if source_version is not None else row["source_version"]
        next_target_version = target_version if target_version is not None else row["target_version"]
        next_recovery_json = recovery_json if recovery is not None else row["recovery_json"]
        conditions = "id = ? AND recovery_generation = ? AND recovery_token = ?"
        parameters: list[Any] = [item_id, recovery_generation, recovery_token]
        if expected_phases:
            placeholders = ",".join("?" for _ in expected_phases)
            conditions += f" AND recovery_phase IN ({placeholders})"
            parameters.extend(sorted(expected_phases))
        cursor = connection.execute(
            "UPDATE remote_operation_items SET source_version = ?, target_version = ?, status = ?, "
            "error_message = ?, recovery_json = ?, recovery_phase = ?, recovery_heartbeat_at = ?, "
            "recovery_token = ?, updated_at = ? WHERE " + conditions,
            (
                next_source_version,
                next_target_version,
                status,
                error_message,
                next_recovery_json,
                recovery_phase,
                now_text,
                None if release_claim else recovery_token,
                now_text,
                *parameters,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RemoteOperationLockError("远程操作恢复状态已被其他任务更新")
        updated = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        connection.commit()
    return _row_to_item(updated)


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
        cursor = connection.execute(
            "UPDATE remote_operation_locks SET heartbeat_at = ?, expires_at = ?, updated_at = ? "
            "WHERE lock_key = ? AND lease_token = ? AND status = 'active' "
            "AND julianday(expires_at) > julianday(?)",
            (now_text, expires_at, now_text, lock_key, lease_token, now_text),
        )
        if cursor.rowcount != 1:
            raise RemoteOperationLockError("远程操作锁租约无效或已过期")
        updated = _fetch_lock(connection, lock_key)
        connection.commit()
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
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO remote_operation_items "
            "(media_source_id, operation_type, idempotency_key, source_path, target_path, "
            "source_version, target_version, status, error_message, recovery_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?) "
            "ON CONFLICT(idempotency_key) DO NOTHING",
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
        row = connection.execute(
            "SELECT * FROM remote_operation_items WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("远程操作幂等记录创建失败")
        item = _row_to_item(row)
        immutablePayload = (
            item.media_source_id,
            item.operation_type,
            item.source_path,
            item.target_path,
            item.source_version,
        )
        requestedPayload = (
            int(media_source_id),
            operation_type,
            source_path,
            target_path,
            source_version,
        )
        if immutablePayload != requestedPayload:
            raise ValueError("远程操作幂等键已用于不同的请求载荷")
        connection.commit()
    return item


def get_remote_operation_item(settings: AppSettings, item_id: int) -> RemoteOperationItem:
    """查询单个远程操作明细。"""

    with closing(_connect(settings)) as connection:
        row = connection.execute(
            "SELECT * FROM remote_operation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
    if row is None:
        raise ValueError("远程操作明细不存在")
    return _row_to_item(row)


def list_remote_operation_items(
    settings: AppSettings,
    *,
    page: int = 1,
    page_size: int = 10,
    media_source_id: int | None = None,
    operation_type: str | None = None,
    status: str | None = None,
) -> RemoteOperationPage:
    """分页查询远程操作明细。"""

    safe_page = max(1, int(page))
    safe_page_size = min(100, max(1, int(page_size)))
    conditions: list[str] = []
    parameters: list[Any] = []
    if media_source_id is not None:
        conditions.append("r.media_source_id = ?")
        parameters.append(int(media_source_id))
    if operation_type:
        conditions.append("r.operation_type = ?")
        parameters.append(operation_type)
    if status:
        conditions.append("r.status = ?")
        parameters.append(status)

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (safe_page - 1) * safe_page_size
    with closing(_connect(settings)) as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM remote_operation_items r{where_clause}",
                parameters,
            ).fetchone()[0]
        )
        rows = connection.execute(
            "SELECT r.*, m.name AS media_source_name "
            "FROM remote_operation_items r "
            "LEFT JOIN media_sources m ON m.id = r.media_source_id"
            f"{where_clause} "
            "ORDER BY r.id DESC LIMIT ? OFFSET ?",
            [*parameters, safe_page_size, offset],
        ).fetchall()
    return RemoteOperationPage(
        items=[_row_to_list_item(row) for row in rows],
        total=total,
        page=safe_page,
        page_size=safe_page_size,
    )


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
