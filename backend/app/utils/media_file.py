"""媒体文件识别工具。

负责判断文件是否属于 M1 支持的视频格式，供扫描流程和测试复用。
"""

from pathlib import Path


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".m4v",
    ".ts",
    ".m2ts",
    ".rmvb",
    ".mpg",
    ".mpeg",
    ".webm",
}


def is_video_file(file_name: str | Path) -> bool:
    """判断文件名是否属于支持的视频格式。

    Args:
        file_name: 文件名或路径。

    Returns:
        如果扩展名属于支持的视频格式，返回 True。
    """

    return Path(file_name).suffix.lower() in VIDEO_EXTENSIONS
