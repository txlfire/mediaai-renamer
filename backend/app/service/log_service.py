"""日志读取服务。

负责将运行时日志转换为页面可查看和可导出的文本。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import zipfile

from app.core.config import AppSettings
from app.service.settings_service import get_effective_settings

LOG_FILE_NAMES = ("batch.log", "app.log", "error.log", "llm.log")


def read_log_items(settings: AppSettings, limit: int = 200) -> list[dict[str, str]]:
    """读取运行日志条目。

    Args:
        settings: 应用运行配置。
        limit: 返回的最大行数。

    Returns:
        日志条目列表。
    """

    items: list[dict[str, str]] = []
    for file_name in LOG_FILE_NAMES:
        log_file = Path(settings.logging.log_dir) / file_name
        if not log_file.exists():
            continue
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-limit:]:
            items.append({"file": file_name, "message": line})
    return items[-limit:]


def export_logs_text(settings: AppSettings) -> str:
    """导出运行日志文本。"""

    lines: list[str] = []
    for item in read_log_items(settings, limit=1000):
        lines.append(f"[{item['file']}] {item['message']}")
    return "\n".join(lines)


def cleanup_logs(settings: AppSettings) -> dict[str, object]:
    """Clean expired log files and archive old rotated logs."""

    effective = get_effective_settings(settings)
    log_dir = Path(str(effective.get("logging.path") or settings.logging.log_dir))
    retention_days = int(effective.get("logging.retention_days") or 30)
    archive_after_days = int(effective.get("logging.archive_after_days") or 7)
    archive_dir = log_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    archive_before = now - timedelta(days=archive_after_days)
    delete_before = now - timedelta(days=retention_days)
    archived_count = 0
    deleted_count = 0
    skipped_count = 0

    if not log_dir.exists():
        return {
            "archived_count": 0,
            "deleted_count": 0,
            "skipped_count": 0,
            "archive_dir": str(archive_dir),
        }

    for path in log_dir.iterdir():
        if path.is_dir() or path.name in LOG_FILE_NAMES:
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified_at < delete_before:
            path.unlink(missing_ok=True)
            deleted_count += 1
            continue
        if modified_at < archive_before and path.suffix.lower() != ".zip":
            archive_path = archive_dir / f"{path.name}.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(path, arcname=path.name)
            path.unlink(missing_ok=True)
            archived_count += 1
            continue
        skipped_count += 1

    return {
        "archived_count": archived_count,
        "deleted_count": deleted_count,
        "skipped_count": skipped_count,
        "archive_dir": str(archive_dir),
    }
