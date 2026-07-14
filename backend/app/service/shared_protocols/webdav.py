"""WebDAV protocol support."""

from __future__ import annotations

import base64
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


def _entry_name_from_href(href: str) -> str:
    stripped = unquote(href).rstrip("/")
    if not stripped:
        return "/"
    return stripped.rsplit("/", 1)[-1]


class WebDavProtocol:
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            protocol="webdav",
            display_name="WebDAV",
            supports_credentials=True,
            supports_directory_browse=True,
            supports_scan=False,
            supports_rename=False,
            requires_system_mount=False,
            can_verify_filesystem_type=False,
            future_candidate=False,
            user_notice="当前支持 HTTPS WebDAV 连接测试和目录浏览；扫描和重命名将在后续步骤接入。",
            remote_capabilities=(
                RemoteProtocolCapability.BROWSE.value,
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
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype /></d:prop></d:propfind>'
        )
        try:
            with _request(path, "PROPFIND", context, body=body) as response:
                payload = response.read()
        except Exception as exc:  # noqa: BLE001 - 目录浏览需要转换为用户可读结果。
            result = _webdav_error_message(exc)
            raise ValueError(result.message) from exc

        root = ET.fromstring(payload)
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
                    path=urljoin(base_url, quote(name, safe="") + "/"),
                    is_directory=True,
                    readable=True,
                    writable=None,
                )
            )
        entries.sort(key=lambda item: item.name.lower())
        parent = base_url.rstrip("/").rsplit("/", 1)[0] + "/" if base_url.rstrip("/") else None
        return DirectoryListing(current_path=base_url, parent_path=parent, entries=entries)

    def check_scan_ready(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        result = self.test_connection(path, context)
        if not result.success:
            return result
        return ConnectionTestResult(False, "WebDAV 扫描尚未启用", "当前阶段仅支持连接测试和目录浏览")

    def check_rename_ready(
        self,
        source_path: str,
        target_path: str,
        context: SharedPathContext | None = None,
    ) -> ConnectionTestResult:
        return ConnectionTestResult(False, "WebDAV 重命名尚未启用", "后续阶段将接入远程操作锁和 MOVE dry-run")

    def normalize_path(self, path: str) -> str:
        return _normalize_webdav_url(path)
