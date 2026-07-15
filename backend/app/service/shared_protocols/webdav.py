"""WebDAV protocol support."""

from __future__ import annotations

import base64
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from app.service.shared_protocols.base import (
    ConnectionTestResult,
    DirectoryEntry,
    DirectoryListing,
    ProtocolCapabilities,
    RemoteProtocolCapability,
    RemoteFileEntry,
    SharedPathContext,
)


def _normalize_webdav_url(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise ValueError("WebDAV 地址不能为空")
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https":
        raise ValueError("WebDAV 仅支持 HTTPS 地址")
    if not parsed.netloc:
        raise ValueError("WebDAV 地址必须包含主机名")
    safe_path = quote(unquote(parsed.path or "/"), safe="/:@")
    rebuilt = parsed._replace(scheme="https", path=safe_path, params="", query="", fragment="")
    return rebuilt.geturl()


def _auth_header(context: SharedPathContext | None) -> str | None:
    if context is None or not context.secret:
        return None
    if context.username:
        token = f"{context.username}:{context.secret}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("ascii")
    return "Bearer " + context.secret


def _request(path: str, method: str, context: SharedPathContext | None, body: bytes | None = None):
    headers = {"User-Agent": "MediaAI-Renamer-WebDAV"}
    auth = _auth_header(context)
    if auth:
        headers["Authorization"] = auth
    if method == "PROPFIND":
        headers["Depth"] = "1"
        headers["Content-Type"] = "application/xml; charset=utf-8"
    request = Request(_normalize_webdav_url(path), data=body, headers=headers, method=method)
    timeout = context.connection_timeout_seconds if context else 5
    return urlopen(request, timeout=timeout)


def _propfind(path: str, context: SharedPathContext | None) -> ET.Element:
    body = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<d:propfind xmlns:d="DAV:"><d:prop>'
        b'<d:resourcetype /><d:getcontentlength />'
        b'<d:getlastmodified /><d:getetag />'
        b'</d:prop></d:propfind>'
    )
    try:
        with _request(path, "PROPFIND", context, body=body) as response:
            payload = response.read()
    except Exception as exc:  # noqa: BLE001 - WebDAV 需要转换为用户可读错误。
        result = _webdav_error_message(exc)
        raise ValueError(result.message) from exc
    return ET.fromstring(payload)


def _propfind_optional(path: str, context: SharedPathContext | None) -> ET.Element | None:
    body = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<d:propfind xmlns:d="DAV:"><d:prop>'
        b'<d:resourcetype /><d:getcontentlength />'
        b'<d:getlastmodified /><d:getetag />'
        b'</d:prop></d:propfind>'
    )
    try:
        with _request(path, "PROPFIND", context, body=body) as response:
            payload = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        result = _webdav_error_message(exc)
        raise ValueError(result.message) from exc
    except Exception as exc:  # noqa: BLE001 - WebDAV 需要转换为用户可读错误。
        result = _webdav_error_message(exc)
        raise ValueError(result.message) from exc
    return ET.fromstring(payload)


def _webdav_error_message(exc: Exception) -> ConnectionTestResult:
    if isinstance(exc, HTTPError):
        if exc.code in (401, 403):
            return ConnectionTestResult(False, "WebDAV 认证失败", "请检查用户名、密码或 Token")
        if exc.code == 404:
            return ConnectionTestResult(False, "WebDAV 路径不存在", "请检查 Base URL 和远程目录")
        return ConnectionTestResult(False, f"WebDAV 返回 HTTP {exc.code}", "请检查 WebDAV 服务状态和反向代理配置")
    if isinstance(exc, URLError):
        return ConnectionTestResult(False, "WebDAV 连接失败", "请检查网络、DNS、证书或代理配置")
    return ConnectionTestResult(False, "WebDAV 连接失败", str(exc))


def _is_collection(response: ET.Element) -> bool:
    for element in response.iter():
        if element.tag.endswith("collection"):
            return True
    return False


def _href_value(response: ET.Element) -> str | None:
    for element in response.iter():
        if element.tag.endswith("href") and element.text:
            return unescape(element.text.strip())
    return None


def _prop_text(response: ET.Element, prop_name: str) -> str | None:
    for element in response.iter():
        if element.tag.endswith(prop_name) and element.text:
            value = element.text.strip()
            if value:
                return value
    return None


def _entry_name_from_href(href: str) -> str:
    stripped = unquote(href).rstrip("/")
    if not stripped:
        return "/"
    return stripped.rsplit("/", 1)[-1]


def _url_from_href(base_url: str, href: str) -> str:
    parsed_href = urlparse(href)
    if parsed_href.scheme:
        return _normalize_webdav_url(href)
    parsed_base = urlparse(base_url)
    safe_path = quote(unquote(parsed_href.path or "/"), safe="/:@")
    return parsed_base._replace(path=safe_path, params="", query="", fragment="").geturl()


def _webdav_modified_at(value: str | None) -> str:
    if not value:
        return "1970-01-01T00:00:00+00:00"
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return "1970-01-01T00:00:00+00:00"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _webdav_file_size(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


class WebDavProtocol:
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            protocol="webdav",
            display_name="WebDAV",
            supports_credentials=True,
            supports_directory_browse=True,
            supports_scan=True,
            supports_rename=False,
            requires_system_mount=False,
            can_verify_filesystem_type=False,
            future_candidate=False,
            user_notice="当前支持 HTTPS WebDAV 连接测试、目录浏览和递归扫描；重命名将在后续步骤接入。",
            remote_capabilities=(
                RemoteProtocolCapability.BROWSE.value,
                RemoteProtocolCapability.SCAN.value,
                RemoteProtocolCapability.READ_METADATA.value,
                RemoteProtocolCapability.ATOMIC_RENAME.value,
                RemoteProtocolCapability.CONDITIONAL_WRITE.value,
                RemoteProtocolCapability.RESUME.value,
            ),
        )

    def validate_config(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        try:
            _normalize_webdav_url(path)
        except ValueError as exc:
            return ConnectionTestResult(False, str(exc), "请输入类似 https://nas.example.com/dav 的地址")
        return ConnectionTestResult(True, "WebDAV 地址格式正确")

    def test_connection(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        validation = self.validate_config(path, context)
        if not validation.success:
            return validation
        try:
            with _request(path, "OPTIONS", context) as response:
                status = int(getattr(response, "status", 200))
        except Exception as exc:  # noqa: BLE001 - 连接测试需要转换为用户可读结果。
            return _webdav_error_message(exc)
        if 200 <= status < 400:
            return ConnectionTestResult(True, "WebDAV 连接成功", readable=True)
        return ConnectionTestResult(False, f"WebDAV 返回 HTTP {status}", "请检查 WebDAV 服务状态")

    def list_directories(self, path: str, context: SharedPathContext | None = None) -> DirectoryListing:
        validation = self.validate_config(path, context)
        if not validation.success:
            raise ValueError(validation.message)
        root = _propfind(path, context)
        base_url = _normalize_webdav_url(path).rstrip("/") + "/"
        base_path = urlparse(base_url).path.rstrip("/") + "/"
        entries: list[DirectoryEntry] = []
        for response in root:
            href = _href_value(response)
            if not href or not _is_collection(response):
                continue
            href_path = urlparse(href).path
            if href_path.rstrip("/") == base_path.rstrip("/"):
                continue
            name = _entry_name_from_href(href)
            entries.append(
                DirectoryEntry(
                    name=name,
                    path=_url_from_href(base_url, href).rstrip("/") + "/",
                    is_directory=True,
                    readable=True,
                    writable=None,
                )
            )
        entries.sort(key=lambda item: item.name.lower())
        parent = base_url.rstrip("/").rsplit("/", 1)[0] + "/" if base_url.rstrip("/") else None
        return DirectoryListing(current_path=base_url, parent_path=parent, entries=entries)

    def list_files(
        self,
        path: str,
        context: SharedPathContext | None = None,
        recursive: bool = True,
    ) -> list[RemoteFileEntry]:
        validation = self.validate_config(path, context)
        if not validation.success:
            raise ValueError(validation.message)

        pending_dirs = [_normalize_webdav_url(path).rstrip("/") + "/"]
        visited_dirs: set[str] = set()
        files: list[RemoteFileEntry] = []
        while pending_dirs:
            current_url = pending_dirs.pop(0)
            if current_url in visited_dirs:
                continue
            visited_dirs.add(current_url)
            root = _propfind(current_url, context)
            current_path = urlparse(current_url).path.rstrip("/") + "/"
            for response in root:
                href = _href_value(response)
                if not href:
                    continue
                entry_url = _url_from_href(current_url, href)
                entry_path = urlparse(entry_url).path.rstrip("/") + ("/" if _is_collection(response) else "")
                if entry_path.rstrip("/") == current_path.rstrip("/"):
                    continue
                if _is_collection(response):
                    if recursive:
                        pending_dirs.append(entry_url.rstrip("/") + "/")
                    continue
                name = _entry_name_from_href(href)
                files.append(
                    RemoteFileEntry(
                        path=entry_url,
                        name=name,
                        extension="." + name.rsplit(".", 1)[-1].lower() if "." in name else "",
                        file_size=_webdav_file_size(_prop_text(response, "getcontentlength")),
                        modified_at=_webdav_modified_at(_prop_text(response, "getlastmodified")),
                        version=_prop_text(response, "getetag"),
                    )
                )
        files.sort(key=lambda item: item.path.lower())
        return files

    def check_scan_ready(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        result = self.test_connection(path, context)
        if not result.success:
            return result
        return ConnectionTestResult(True, "WebDAV 扫描可用", readable=True)

    def check_rename_ready(
        self,
        source_path: str,
        target_path: str,
        context: SharedPathContext | None = None,
    ) -> ConnectionTestResult:
        source_validation = self.validate_config(source_path, context)
        if not source_validation.success:
            return source_validation
        target_validation = self.validate_config(target_path, context)
        if not target_validation.success:
            return target_validation

        try:
            source_resource = _propfind_optional(source_path, context)
            if source_resource is None:
                return ConnectionTestResult(False, "WebDAV 源文件不存在", "请重新扫描后再生成重命名预览")
            if any(_is_collection(response) for response in source_resource):
                return ConnectionTestResult(False, "WebDAV 源路径不是文件", "当前仅支持媒体文件重命名 dry-run")
            target_resource = _propfind_optional(target_path, context)
        except ValueError as exc:
            return ConnectionTestResult(False, str(exc), "请检查 WebDAV 服务状态、权限或代理配置")

        if target_resource is not None:
            return ConnectionTestResult(False, "WebDAV 目标文件已存在", "请修改目标文件名后重新 dry-run")

        return ConnectionTestResult(
            True,
            "WebDAV MOVE dry-run 可执行",
            "真实 MOVE 将在后续阶段接入远程操作锁后启用",
            readable=True,
            writable=True,
        )

    def normalize_path(self, path: str) -> str:
        return _normalize_webdav_url(path)
