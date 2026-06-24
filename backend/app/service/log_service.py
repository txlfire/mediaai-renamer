"""日志读取服务。

负责将运行时日志转换为页面可查看和可导出的文本。
"""

from pathlib import Path

from app.core.config import AppSettings

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
