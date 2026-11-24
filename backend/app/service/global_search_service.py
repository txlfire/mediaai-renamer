"""本地只读搜索；显式白名单、稳定游标和 SQLite 执行预算。"""

from contextlib import closing
import sqlite3
import time
from threading import BoundedSemaphore
import unicodedata

from app.service.background_job_service import KINDS as BACKGROUND_PERMISSIONS

KINDS = ("source", "scan", "file", "preview", "background")
SEARCH_SLOTS = BoundedSemaphore(2)


class SearchUnavailable(Exception):
    """查询超出预算或本地存储暂不可读，不返回不完整结果。"""


def normalizeQuery(value):
    if not isinstance(value, str):
        raise ValueError("search_invalid_query")
    value = unicodedata.normalize("NFC", value.strip())
    if not 2 <= len(value) <= 128 or any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("search_invalid_query")
    return value


def _statement(kind, user):
    # SQL 结构只来自内部枚举，所有用户输入均绑定参数。
    common = "NULL AS sourceId,NULL AS scanJobId,NULL AS requestId"
    if kind == "source":
        return (f"SELECT s.id,s.name AS title,s.protocol AS subtitle,CASE s.enabled WHEN 1 THEN 'enabled' ELSE 'disabled' END AS status,{common} FROM media_sources s",
                "s", ["s.name", "s.protocol"], [], [])
    if kind == "scan":
        return ("SELECT s.id,'扫描任务 #'||s.id AS title,m.name AS subtitle,s.status,s.media_source_id AS sourceId,s.id AS scanJobId,NULL AS requestId FROM scan_jobs s JOIN media_sources m ON m.id=s.media_source_id",
                "s", ["CAST(s.id AS TEXT)", "m.name", "s.status"], [], [])
    if kind == "file":
        return ("SELECT s.id,s.file_name AS title,m.name AS subtitle,'indexed' AS status,s.media_source_id AS sourceId,s.scan_job_id AS scanJobId,NULL AS requestId FROM media_files s JOIN media_sources m ON m.id=s.media_source_id",
                "s", ["s.file_name"], [], [])
    if kind == "preview":
        return ("SELECT s.id,s.parsed_title AS title,s.suggested_name AS subtitle,s.status,f.media_source_id AS sourceId,f.scan_job_id AS scanJobId,NULL AS requestId FROM rename_previews s JOIN media_files f ON f.id=s.media_file_id",
                "s", ["s.parsed_title", "s.suggested_name", "s.edited_name", "f.file_name"], [], [])
    permitted = [key for key, permission in BACKGROUND_PERMISSIONS.items() if permission in user.permissions]
    placeholders = ",".join("?" for _ in permitted) or "NULL"
    return (f"SELECT s.id,s.kind AS title,'后台任务 #'||s.id AS subtitle,s.status,NULL AS sourceId,NULL AS scanJobId,s.request_id AS requestId FROM background_jobs s",
            "s", ["s.request_id", "CAST(s.id AS TEXT)", "s.kind", "s.status"],
            [f"s.actor_id=?", f"s.kind IN ({placeholders})"], [user.id, *permitted])


def _public(row, kind):
    result = {"kind": kind, **{key: row[key] for key in ("id", "title", "subtitle", "status", "sourceId", "scanJobId", "requestId")}}
    for key in ("title", "subtitle", "status"):
        result[key] = str(result[key] or "")[:512]
    return result


def searchLocal(settings, user, query, *, kind="all", limit=10, beforeId=None, recordId=None,
                timeBudget=0.6, stepBudget=3_000_000):
    """不建索引、不触达媒体；单事务快照内按 ID 倒序检索，超时整次失败。"""
    if kind not in (*KINDS, "all") or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("search_invalid_parameters")
    for value in (beforeId, recordId):
        if value is not None and (type(value) is not int or not 1 <= value <= 2**63 - 1):
            raise ValueError("search_invalid_parameters")
    if (beforeId is not None or recordId is not None) and kind == "all":
        raise ValueError("search_invalid_parameters")
    normalized = normalizeQuery(query) if recordId is None else None
    exactId = int(normalized[1:]) if normalized and normalized.startswith("#") and normalized[1:].isascii() and normalized[1:].isdigit() else None
    if exactId is not None and not 1 <= exactId <= 2**63 - 1:
        raise ValueError("search_invalid_parameters")
    pattern = "%" + normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" if normalized else None
    started = time.monotonic()
    steps = 0

    def interrupted():
        nonlocal steps
        steps += 1000
        return steps > stepBudget or time.monotonic() - started > timeBudget

    if not SEARCH_SLOTS.acquire(blocking=False):
        raise SearchUnavailable("search_unavailable")
    try:
        with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.15)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(interrupted, 1000)
            connection.execute("BEGIN")
            groups = []
            for selected in KINDS if kind == "all" else (kind,):
                sql, alias, fields, conditions, parameters = _statement(selected, user)
                if recordId is not None or exactId is not None:
                    conditions.append(f"{alias}.id=?")
                    parameters.append(recordId if recordId is not None else exactId)
                else:
                    conditions.append("(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in fields) + ")")
                    parameters.extend([pattern] * len(fields))
                if beforeId is not None:
                    conditions.append(f"{alias}.id<?")
                    parameters.append(beforeId)
                rows = connection.execute(sql + " WHERE " + " AND ".join(conditions) + f" ORDER BY {alias}.id DESC LIMIT ?",
                                          [*parameters, limit + 1]).fetchall()
                items = [_public(row, selected) for row in rows[:limit]]
                groups.append({"kind": selected, "items": items, "hasMore": len(rows) > limit,
                               "nextBeforeId": items[-1]["id"] if len(rows) > limit else None})
            return {"groups": groups, "networkAccess": False}
    except sqlite3.Error as exc:
        raise SearchUnavailable("search_unavailable") from exc
    finally:
        SEARCH_SLOTS.release()
