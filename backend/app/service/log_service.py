"""日志读取服务。

负责将运行时日志转换为页面可查看和可导出的文本。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import re
import sqlite3
from contextlib import closing
import zipfile

from app.core.config import AppSettings
from app.core.logger import LOG_DIRECTORY_LOCK_FILE, guardLogFileMutation
from app.service.settings_service import get_effective_settings
from app.utils.redaction import redactUrlCredentials

LOG_FILE_NAMES = ("batch.log", "app.log", "error.log", "llm.log")
MAX_TAIL_BYTES = 256 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_CLEANUP_FILES = 100
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
ROTATED_LOG_PATTERN = re.compile(r"^(?:batch|app|error|llm)\.log\.[0-9][0-9._-]*$")
LOG_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[,.]\d{3,6})?")


def _fileIdentity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        if path.is_symlink():
            return None
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


def _removeUnchanged(path: Path, identity: tuple[int, int, int, int] | None, logDir: Path) -> bool:
    with guardLogFileMutation(logDir):
        if identity is None or _fileIdentity(path) != identity:
            return False
        path.unlink()
        return True


def _logDirectory(settings: AppSettings) -> Path:
    return Path(str(get_effective_settings(settings).get("logging.path") or settings.logging.log_dir))


def _fileInventory(logDir: Path) -> tuple[list[tuple[Path, int, float, bool]], int]:
    managed = []
    unknownBytes = 0
    for directory, archive in ((logDir, False), (logDir / "archive", True)):
        if not directory.exists() or (archive and directory.is_symlink()):
            continue
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            if not archive and path.name == LOG_DIRECTORY_LOCK_FILE:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            active = not archive and path.name in LOG_FILE_NAMES
            name = path.name[:-4] if archive and path.suffix == ".zip" else path.name
            rotated = bool(ROTATED_LOG_PATTERN.fullmatch(name)) and (not archive or path.suffix == ".zip")
            if active or rotated:
                managed.append((path, stat.st_size, stat.st_mtime, active))
            else:
                unknownBytes += stat.st_size
    return managed, unknownBytes


def getLogStorageStats(settings: AppSettings) -> dict:
    effective = get_effective_settings(settings)
    inventory, unknownBytes = _fileInventory(Path(str(effective["logging.path"])))
    with closing(sqlite3.connect(settings.database_path)) as connection:
        logicalBytes = connection.execute("SELECT COALESCE(SUM(approx_bytes), 0) FROM operation_logs").fetchone()[0]
        freePages = connection.execute("PRAGMA freelist_count").fetchone()[0]
        pageSize = connection.execute("PRAGMA page_size").fetchone()[0]
    def fileBytes(path: Path) -> int:
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0
    managedBytes = sum(item[1] for item in inventory)
    limit = int(effective["logging.max_total_mb"]) * 1024 * 1024
    return {"managed_file_bytes": managedBytes, "unknown_file_bytes": unknownBytes, "file_limit_bytes": limit,
            "file_over_budget": managedBytes > limit, "operation_log_bytes": int(logicalBytes),
            "database_file_bytes": fileBytes(settings.database_path),
            "wal_file_bytes": fileBytes(Path(str(settings.database_path) + "-wal")),
            "database_reusable_bytes": int(freePages) * int(pageSize),
            "message": "数据库日志删除后空间可复用，但数据库文件不一定缩小；不会在线自动执行 VACUUM。"}


def _tailLog(path: Path, limit: int) -> tuple[list[str], bool]:
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        offset = max(0, size - MAX_TAIL_BYTES)
        stream.seek(offset)
        data = stream.read(MAX_TAIL_BYTES)
    lines = data.splitlines()
    truncated = offset > 0
    if offset and lines:
        # 开头可能落在半行或 URL 凭据内部，不展示不完整行片段。
        lines.pop(0)
    truncated = truncated or len(lines) > limit
    result = []
    for line in lines[-limit:]:
        if len(line) > MAX_LINE_BYTES:
            truncated = True
            result.append("[单条日志超过显示上限，已省略]")
        else:
            result.append(redactUrlCredentials(line.decode("utf-8", errors="replace")))
    return result, truncated


def read_log_page(settings: AppSettings, limit: int | None = None) -> dict:
    if limit is None:
        limit = int(get_effective_settings(settings)["operations.log_default_limit"])
    if not 1 <= limit <= 10000:
        raise ValueError("日志显示数量必须为 1-10000")
    logDir = _logDirectory(settings)
    items = []
    truncated = False
    unavailable = False
    for fileName in LOG_FILE_NAMES:
        path = logDir / fileName
        if path.is_symlink():
            unavailable = True
            continue
        try:
            lines, trimmed = _tailLog(path, limit)
        except FileNotFoundError:
            continue
        except OSError:
            unavailable = True
            continue
        truncated = truncated or trimmed
        timestamp = ""
        for line in lines:
            matched = LOG_TIMESTAMP_PATTERN.match(line)
            if matched:
                timestamp = matched.group().replace(",", ".")
            # 多行异常沿用前一行时间；未知时间保持稳定顺序，不猜测文件mtime。
            items.append((timestamp, {"file": fileName, "message": line}))
    truncated = truncated or len(items) > limit
    items.sort(key=lambda item: item[0])
    items = [item[1] for item in items[-limit:]]
    message = "仅显示日志尾部，超出行数、字节或单条显示上限的内容已省略。" if truncated else None
    if unavailable:
        message = (message or "") + "部分日志不可读取或不是受支持的普通文件。"
    return {"items": items, "truncated": truncated, "message": message}


def read_log_items(settings: AppSettings, limit: int | None = None) -> list[dict[str, str]]:
    """读取运行日志条目。

    Args:
        settings: 应用运行配置。
        limit: 返回的最大行数。

    Returns:
        日志条目列表。
    """

    return read_log_page(settings, limit)["items"]


def export_logs_text(settings: AppSettings) -> str:
    """导出运行日志文本。"""

    lines: list[str] = []
    page = read_log_page(settings, limit=1000)
    if page["message"]:
        lines.append(page["message"])
    totalBytes = sum(len(line.encode("utf-8")) + 1 for line in lines)
    for item in page["items"]:
        line = f"[{item['file']}] {item['message']}"
        size = len(line.encode("utf-8")) + 1
        if totalBytes + size > 1024 * 1024 - 512:
            lines.insert(0, "[日志导出达到1MB上限，后续内容已省略。]")
            break
        lines.append(line)
        totalBytes += size
    return "\n".join(lines)


def cleanup_logs(settings: AppSettings) -> dict[str, object]:
    """有界清理已过期日志；文件在归档期间变化则保留并重试。"""

    effective = get_effective_settings(settings)
    log_dir = Path(str(effective.get("logging.path") or settings.logging.log_dir))
    retention_days = int(effective.get("logging.retention_days") or 30)
    archive_after_days = int(effective.get("logging.archive_after_days") or 7)
    archive_dir = log_dir / "archive"

    now = datetime.now(timezone.utc)
    archive_before = now - timedelta(days=archive_after_days)
    delete_before = now - timedelta(days=retention_days)
    archived_count = 0
    deleted_count = 0
    skipped_count = 0
    processed = 0
    hasMore = False
    archiveBytes = 0
    maxTotalBytes = int(effective.get("logging.max_total_mb") or 512) * 1024 * 1024

    if not log_dir.exists():
        return {
            "archived_count": 0,
            "deleted_count": 0,
            "skipped_count": 0,
            "archive_dir": str(archive_dir),
        }

    if archive_dir.is_symlink():
        raise ValueError("日志归档目录不允许使用符号链接")
    for directory, isArchive in ((archive_dir, True), (log_dir, False)):
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name[:-4] if isArchive and path.suffix == ".zip" else path.name
            if not ROTATED_LOG_PATTERN.fullmatch(name) or (isArchive and path.suffix != ".zip"):
                continue
            if processed >= MAX_CLEANUP_FILES:
                hasMore = True
                break
            identity = _fileIdentity(path)
            if identity is None:
                continue
            modified_at = datetime.fromtimestamp(identity[3] / 1_000_000_000, timezone.utc)
            if modified_at < delete_before:
                if not _removeUnchanged(path, identity, log_dir):
                    skipped_count += 1
                    hasMore = True
                    continue
                deleted_count += 1
                processed += 1
            elif not isArchive and modified_at < archive_before:
                fileSize = identity[2]
                if fileSize > MAX_ARCHIVE_BYTES:
                    skipped_count += 1
                    continue
                if archiveBytes + fileSize > MAX_ARCHIVE_BYTES:
                    hasMore = True
                    continue
                archive_path = archive_dir / f"{path.name}.zip"
                created = False
                with guardLogFileMutation(log_dir):
                    if _fileIdentity(path) != identity:
                        skipped_count += 1
                        hasMore = True
                        continue
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        # 不覆盖已有归档；保留原始日志时间，压缩不延长保留期。
                        with archive_path.open("xb") as target:
                            created = True
                            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                                archive.write(path, arcname=path.name)
                        if _fileIdentity(path) != identity:
                            archive_path.unlink(missing_ok=True)
                            created = False
                            skipped_count += 1
                            hasMore = True
                            continue
                        os.utime(archive_path, ns=(identity[3], identity[3]))
                        path.unlink()
                    except FileExistsError:
                        skipped_count += 1
                        continue
                    except Exception:
                        if created:
                            archive_path.unlink(missing_ok=True)
                        raise
                if not created:
                    skipped_count += 1
                    hasMore = True
                    continue
                archived_count += 1
                processed += 1
                archiveBytes += fileSize
            else:
                skipped_count += 1

    inventory, unknownBytes = _fileInventory(log_dir)
    managedBytes = sum(item[1] for item in inventory)
    candidates = sorted((item for item in inventory if not item[3]),
                        key=lambda item: (item[0].name.startswith("error.log"), item[2], item[0].name))
    for path, size, _, _ in candidates:
        if managedBytes <= maxTotalBytes:
            break
        if processed >= MAX_CLEANUP_FILES:
            hasMore = True
            break
        identity = _fileIdentity(path)
        if identity is None or identity[2] != size or not _removeUnchanged(path, identity, log_dir):
            skipped_count += 1
            hasMore = True
            continue
        managedBytes -= size
        deleted_count += 1
        processed += 1
    inventory, unknownBytes = _fileInventory(log_dir)
    managedBytes = sum(item[1] for item in inventory)

    return {
        "archived_count": archived_count,
        "deleted_count": deleted_count,
        "skipped_count": skipped_count,
        "archive_dir": str(archive_dir),
        "has_more": hasMore,
        "managed_file_bytes": managedBytes,
        "unknown_file_bytes": unknownBytes,
        "file_limit_bytes": maxTotalBytes,
        "over_budget": managedBytes > maxTotalBytes,
        "message": "活动日志不删除；本轮有界清理后仍可能超限，请核对轮转设置和后续维护结果。" if managedBytes > maxTotalBytes or hasMore else None,
    }
