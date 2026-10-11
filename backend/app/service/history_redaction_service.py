"""停服维护用历史 URL 凭据脱敏，不接触媒体文件或凭据配置。"""

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

from app.core.config import AppSettings
from app.utils.redaction import redactUrlCredentials
from app.utils.file_permissions import copyFileAccessControl

_LOG_NAMES = ("app.log", "batch.log", "error.log", "llm.log")
_MAX_LINE_BYTES = 1024 * 1024


@dataclass
class RedactionReport:
    apply: bool
    databaseRows: int = 0
    matchedFiles: int = 0
    changedFiles: int = 0
    skippedFiles: int = 0
    failedFiles: int = 0
    auditStatus: str = "not_written"

    def toDict(self) -> dict[str, object]:
        return asdict(self)


def _redactStream(source, target=None) -> bool:
    changed = False
    while line := source.readline(_MAX_LINE_BYTES + 1):
        if len(line) > _MAX_LINE_BYTES:
            raise ValueError("日志单行超出维护上限，原文件保留")
        text = line.decode("utf-8", errors="strict")
        sanitized = redactUrlCredentials(text)
        changed = changed or sanitized != text
        if target is not None:
            target.write(sanitized.encode("utf-8"))
    return changed


def _isLogName(name: str) -> bool:
    return any(name == base or name.startswith(base + ".") for base in _LOG_NAMES)


def _copyAccessControl(source: Path, target: Path) -> None:
    copyFileAccessControl(source, target)


def _redactFile(path: Path, *, apply: bool, maxBytes: int) -> bool:
    original = path.stat()
    if original.st_size > maxBytes or path.suffix.lower() == ".gz":
        raise ValueError("文件超出维护范围，原文件保留")
    tempPath = None
    try:
        if apply:
            fd, name = tempfile.mkstemp(prefix=".redaction-", dir=path.parent)
            os.close(fd)
            tempPath = Path(name)
            _copyAccessControl(path, tempPath)
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as sourceArchive:
                entries = sourceArchive.infolist()
                if len(entries) > 256 or sum(entry.file_size for entry in entries) > maxBytes:
                    raise ValueError("压缩包展开后超过维护上限")
                if any(entry.is_dir() or entry.flag_bits & 1 or not _isLogName(Path(entry.filename).name) for entry in entries):
                    raise ValueError("压缩包含非日志条目，原文件保留")
                if apply:
                    with zipfile.ZipFile(tempPath, "w", compression=zipfile.ZIP_DEFLATED) as outputArchive:
                        changed = False
                        for entry in entries:
                            with sourceArchive.open(entry) as stream, outputArchive.open(entry, "w") as output:
                                changed = _redactStream(stream, output) or changed
                else:
                    changed = False
                    for entry in entries:
                        with sourceArchive.open(entry) as stream:
                            changed = _redactStream(stream) or changed
        else:
            with path.open("rb") as stream:
                if apply:
                    with tempPath.open("wb") as output:
                        changed = _redactStream(stream, output)
                        output.flush()
                        os.fsync(output.fileno())
                else:
                    changed = _redactStream(stream)
        if apply and changed:
            current = path.stat()
            if (current.st_size, current.st_mtime_ns, current.st_ino) != (
                original.st_size, original.st_mtime_ns, original.st_ino
            ):
                raise ValueError("日志在维护期间变化，原文件保留，请停服后重试")
            shutil.copystat(path, tempPath)
            os.replace(tempPath, path)
        return changed
    finally:
        if tempPath is not None:
            tempPath.unlink(missing_ok=True)


def _redactDatabase(connection: sqlite3.Connection, *, apply: bool) -> int:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    changed = 0
    for table, fields in (("audit_events", ("summary", "detail_json")), ("operation_logs", ("message", "detail_json"))):
        if table not in tables:
            continue
        lastId = 0
        while True:
            rows = connection.execute(
                f"SELECT id, {', '.join(fields)} FROM {table} WHERE id > ? ORDER BY id LIMIT 500", (lastId,)
            ).fetchall()
            if not rows:
                break
            for row in rows:
                lastId = row[0]
                sanitized = [redactUrlCredentials(value) if value is not None else None for value in row[1:]]
                if sanitized == list(row[1:]):
                    continue
                changed += 1
                if apply:
                    assignments = ", ".join(f"{field} = ?" for field in fields)
                    if table == "operation_logs":
                        approxBytes = sum(len(value.encode("utf-8")) for value in sanitized if value is not None)
                        connection.execute(
                            f"UPDATE {table} SET {assignments}, approx_bytes = ? WHERE id = ?",
                            (*sanitized, approxBytes, lastId),
                        )
                    else:
                        connection.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*sanitized, lastId))
    return changed


def _recordMaintenance(connection: sqlite3.Connection, result: str, summary: str) -> None:
    connection.execute(
        "INSERT INTO audit_events(event_type, actor_name, target_type, action, result, summary, created_at) "
        "VALUES ('security.maintenance', 'local-maintenance', 'logs', 'redact_history', ?, ?, ?)",
        (result, summary, datetime.now(timezone.utc).isoformat()),
    )


def redactHistory(
    settings: AppSettings, *, apply: bool = False, serviceStopped: bool = False,
    maxFileMb: int = 128,
) -> RedactionReport:
    if apply and not serviceStopped:
        raise ValueError("执行脱敏前必须确认服务已停止")
    if not 1 <= maxFileMb <= 1024:
        raise ValueError("单文件维护上限必须在 1-1024 MB 之间")
    databasePath = settings.database_path.resolve()
    if not databasePath.is_file() or settings.database_path.is_symlink():
        raise ValueError("已有数据库不存在或是符号链接，不进行维护")
    report = RedactionReport(apply=apply)
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(databasePath.as_uri() + f"?mode={mode}", uri=True, timeout=1)) as connection:
        if apply:
            connection.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        logDir = settings.logging.log_dir
        if "system_settings" in tables:
            row = connection.execute("SELECT value FROM system_settings WHERE key='logging.path'").fetchone()
            if row and row[0].strip():
                logDir = Path(row[0])
        if logDir.is_symlink():
            raise ValueError("日志根目录不能是符号链接")
        report.databaseRows = _redactDatabase(connection, apply=apply)
        if apply:
            if "audit_events" in tables:
                _recordMaintenance(connection, "started", "本机历史 URL 凭据脱敏已授权；数据库更新与此记录同事务")
                report.auditStatus = "started"
            else:
                report.auditStatus = "unavailable"
            connection.commit()
    # 数据库和每个文件独立提交；文件失败不伪装成全局回滚。
    root = logDir.resolve()
    directories = [root] if root.is_dir() else []
    archiveDir = root / "archive"
    if archiveDir.is_dir() and not archiveDir.is_symlink():
        directories.append(archiveDir)
    for directory in directories:
        try:
            paths = directory.iterdir()
            # 在目录枚举阶段失败时也保留已提交的数据库结果和维护审计。
            for path in paths:
                if not _isLogName(path.name):
                    continue
                if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                    report.skippedFiles += 1
                    continue
                try:
                    changed = _redactFile(path, apply=apply, maxBytes=maxFileMb * 1024 * 1024)
                except (ValueError, UnicodeError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
                    report.skippedFiles += 1
                    continue
                except OSError:
                    report.failedFiles += 1
                    continue
                if changed:
                    report.matchedFiles += 1
                    report.changedFiles += int(apply)
        except OSError:
            report.failedFiles += 1
    if apply and report.auditStatus == "started":
        try:
            with closing(sqlite3.connect(databasePath, timeout=1)) as connection:
                result = "partial" if report.failedFiles or report.skippedFiles else "success"
                _recordMaintenance(connection, result, f"历史脱敏完成：数据库 {report.databaseRows} 条，文件 {report.changedFiles} 个，跳过 {report.skippedFiles} 个，失败 {report.failedFiles} 个")
                connection.commit()
            report.auditStatus = "complete"
        except sqlite3.Error:
            report.auditStatus = "completion_failed"
    return report
