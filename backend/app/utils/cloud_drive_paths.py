"""CD2 路径只按目录段比较，不借用本地文件系统的大小写或归一化规则。"""

import re
import unicodedata
from urllib.parse import urlsplit


def canonicalCd2Target(endpoint: str) -> str:
    try:
        parts = urlsplit(endpoint)
        port = 443 if parts.port is None else parts.port
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username is not None or parts.password is not None
                or parts.path not in {"", "/"} or parts.query or parts.fragment
                or not 1 <= port <= 65535 or any(char.isspace() or ord(char) < 33 for char in endpoint)
                or "\\" in endpoint or "%" in host):
            raise ValueError
        host = host.encode("idna").decode("ascii").lower()
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("CD2端点须为不含账号、路径、查询或片段的HTTPS地址") from None
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def canonicalCloudPath(value: str) -> str:
    if (not isinstance(value, str) or not value.startswith("/")
            or len(value.encode("utf-8", errors="replace")) > 4096
            or "\\" in value or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
            or re.search(r"%(?:2f|5c|2e|00|25)", value, re.IGNORECASE)):
        raise ValueError("云目录须为不含控制字符、转义分隔符的绝对路径")
    path = value.rstrip("/") or "/"
    if path != "/" and any(part in {"", ".", ".."} for part in path[1:].split("/")):
        raise ValueError("云目录不能包含空目录段或相对目录段")
    if value.startswith("//"):
        raise ValueError("云目录不能以双斜杠开头")
    return path


def approvedCloudRoot(value: str) -> str:
    path = canonicalCloudPath(value)
    if len(path.strip("/").split("/")) < 2 or path == "/":
        raise ValueError("须限定到云盘下的媒体目录，不能选择全局根目录或云盘根目录")
    return path


def isWithinCloudRoot(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")
