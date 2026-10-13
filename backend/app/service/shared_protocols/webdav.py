"""WebDAV protocol support."""

from __future__ import annotations

import base64
import re
import secrets
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener
import xml.etree.ElementTree as ET

from app.core.logger import get_logger

MAX_DAV_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class WebDavCapabilityVerification:
    status: str
    message: str
    suggestion: str | None = None
    cleanup_succeeded: bool = False
    evidence: dict[str, int] | None = None


class _UnsupportedConditionalMove(RuntimeError):
    pass


class _DavTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        # DAV目录响应不需要DTD；在展开实体前拒绝，兼容XML声明的字符编码。
        raise ValueError("WebDAV 响应不允许包含 DTD")


def _successfulProperties(response: ET.Element) -> list[ET.Element]:
    result = []
    for propstat in response.findall("{DAV:}propstat"):
        status = propstat.findtext("{DAV:}status")
        if status:
            parts = status.split()
            if len(parts) < 2 or not parts[1].isdigit() or not 200 <= int(parts[1]) < 300:
                continue
        prop = propstat.find("{DAV:}prop")
        if prop is not None:
            result.append(prop)
    return result

from app.service.shared_protocols.base import (
    ConnectionTestResult,
    DirectoryEntry,
    DirectoryListing,
    ProtocolCapabilities,
    RemoteProtocolCapability,
    RemoteFileEntry,
    SharedPathContext,
)


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 不将认证头交给跳转目标；管理员应配置最终的 WebDAV 地址。
        raise ValueError("WebDAV 不允许自动重定向，请配置最终地址")


def urlopen(request, timeout=5):
    return build_opener(_RejectRedirect()).open(request, timeout=timeout)


def _normalize_webdav_url(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise ValueError("WebDAV 地址不能为空")
    if any(ord(char) < 32 or ord(char) == 127 for char in path) or "\\" in path:
        raise ValueError("WebDAV 地址包含非法字符")
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https":
        raise ValueError("WebDAV 仅支持 HTTPS 地址")
    if not parsed.netloc:
        raise ValueError("WebDAV 地址必须包含主机名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("WebDAV 地址不能内嵌凭据，请使用独立用户名和密码字段")
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.params:
        raise ValueError("WebDAV 地址不能包含查询参数、片段或路径参数")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("WebDAV 端口无效") from exc
    rawPath = parsed.path or "/"
    decodedPath = unquote(rawPath, errors="strict")
    if (
        re.search(r"%(?:2f|5c)", rawPath, re.IGNORECASE)
        or re.search(r"%[0-9a-f]{2}", decodedPath, re.IGNORECASE)
        or "\\" in decodedPath
        or any(ord(char) < 32 or ord(char) == 127 for char in decodedPath)
        or any(part in {".", ".."} for part in decodedPath.split("/"))
    ):
        raise ValueError("WebDAV 地址包含不安全的路径编码或目录跳转")
    safe_path = quote(decodedPath, safe="/:@")
    rebuilt = parsed._replace(scheme="https", path=safe_path, params="", query="", fragment="")
    return rebuilt.geturl()


def _confined_url(path: str, rootUrl: str) -> str:
    normalized = _normalize_webdav_url(path)
    root = urlparse(_normalize_webdav_url(rootUrl))
    target = urlparse(normalized)
    if (target.scheme, target.hostname.lower(), target.port or 443) != (
        root.scheme, root.hostname.lower(), root.port or 443
    ):
        raise ValueError("WebDAV 地址不允许跨站点或跨端口访问")
    rootPath = unquote(root.path).rstrip("/")
    targetPath = unquote(target.path).rstrip("/")
    if targetPath != rootPath and not targetPath.startswith(rootPath + "/"):
        raise ValueError("WebDAV 地址超出允许的根目录")
    return normalized


def _move_root(sourcePath: str, context: SharedPathContext | None) -> str:
    return context.root_url if context and context.root_url else sourcePath.rsplit("/", 1)[0] + "/"


def _auth_header(context: SharedPathContext | None) -> str | None:
    if context is None or not context.secret:
        return None
    if context.username:
        token = f"{context.username}:{context.secret}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("ascii")
    return "Bearer " + context.secret


def _request(
    path: str,
    method: str,
    context: SharedPathContext | None,
    body: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
):
    normalized = _normalize_webdav_url(path)
    if context and context.root_url:
        normalized = _confined_url(normalized, context.root_url)
    headers = {"User-Agent": "MediaAI-Renamer-WebDAV"}
    auth = _auth_header(context)
    if auth:
        headers["Authorization"] = auth
    if method == "PROPFIND":
        headers["Depth"] = "1"
        headers["Content-Type"] = "application/xml; charset=utf-8"
    if extra_headers:
        destinations = [value for key, value in extra_headers.items() if key.lower() == "destination"]
        normalizedDestinations = {
            _confined_url(value, _move_root(normalized, context)) for value in destinations
        }
        if len(normalizedDestinations) > 1:
            raise ValueError("WebDAV Destination 重复且目标不一致")
        headers.update({key: value for key, value in extra_headers.items() if key.lower() != "destination"})
        if normalizedDestinations:
            headers["Destination"] = normalizedDestinations.pop()
    request = Request(normalized, data=body, headers=headers, method=method)
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
            payload = response.read(MAX_DAV_RESPONSE_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 - WebDAV 需要转换为用户可读错误。
        result = _webdav_error_message(exc)
        raise ValueError(result.message) from exc
    return _parseDavPayload(payload)


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
            payload = response.read(MAX_DAV_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            _closeHttpError(exc)
            return None
        result = _webdav_error_message(exc)
        raise ValueError(result.message) from exc
    except Exception as exc:  # noqa: BLE001 - WebDAV 需要转换为用户可读错误。
        result = _webdav_error_message(exc)
        raise ValueError(result.message) from exc
    return _parseDavPayload(payload)


def _parseDavPayload(payload: bytes) -> ET.Element:
    if len(payload) > MAX_DAV_RESPONSE_BYTES:
        raise ValueError("WebDAV 目录响应超过8MB上限，请缩小媒体源目录范围")
    try:
        root = ET.fromstring(payload, parser=ET.XMLParser(target=_DavTreeBuilder()))
        if root.tag != "{DAV:}multistatus":
            raise ValueError("WebDAV 响应不是 DAV multistatus")
        for response in root.findall("{DAV:}response"):
            status = response.findtext("{DAV:}status")
            if status:
                parts = status.split()
                if len(parts) < 2 or not parts[1].isdigit() or not 200 <= int(parts[1]) < 300:
                    raise ValueError("WebDAV 资源状态未成功")
            if response.findall("{DAV:}propstat") and not _successfulProperties(response):
                raise ValueError("WebDAV 资源属性不可读取")
        return root
    except (ET.ParseError, ValueError) as exc:
        try:
            get_logger(__name__).warning("WebDAV响应格式无效 response_bytes=%s error_type=%s", len(payload), type(exc).__name__)
        except Exception:  # noqa: BLE001 - 辅助日志不可覆盖格式错误。
            pass
        raise ValueError("WebDAV 服务响应格式无效，请检查地址或反向代理配置") from exc


def _closeHttpError(exc: HTTPError) -> None:
    try:
        exc.close()
    except Exception:  # noqa: BLE001 - 关闭失败不能覆盖原始HTTP结果。
        pass


def _webdav_error_message(exc: Exception) -> ConnectionTestResult:
    if isinstance(exc, HTTPError):
        _closeHttpError(exc)
        if exc.code in (401, 403):
            return ConnectionTestResult(False, "WebDAV 认证失败", "请检查用户名、密码或 Token")
        if exc.code == 404:
            return ConnectionTestResult(False, "WebDAV 路径不存在", "请检查 Base URL 和远程目录")
        if exc.code == 412:
            return ConnectionTestResult(
                False,
                "WebDAV 条件移动被拒绝（HTTP 412）",
                "源文件版本可能已变化，或目标文件已经存在，请重新扫描并生成预览",
            )
        return ConnectionTestResult(False, f"WebDAV 返回 HTTP {exc.code}", "请检查 WebDAV 服务状态和反向代理配置")
    if isinstance(exc, URLError):
        return ConnectionTestResult(False, "WebDAV 连接失败", "请检查网络、DNS、证书或代理配置")
    if isinstance(exc, ValueError):
        return ConnectionTestResult(False, "WebDAV 地址或安全边界校验失败", "请检查最终地址、根目录及路径编码，不允许自动重定向")
    return ConnectionTestResult(False, "WebDAV 连接失败", "请检查服务配置，详细错误未回显以避免泄露凭据")


def _is_collection(response: ET.Element) -> bool:
    return any(prop.find("{DAV:}resourcetype/{DAV:}collection") is not None
               for prop in _successfulProperties(response))


def _href_value(response: ET.Element) -> str | None:
    value = response.findtext("{DAV:}href")
    return unescape(value.strip()) if value else None


def _requireDirectoryListing(root: ET.Element, path: str) -> None:
    normalizedPath = _normalize_webdav_url(path).rstrip("/")
    directoryConfirmed = False
    for response in root.findall("{DAV:}response"):
        href = _href_value(response)
        if not href:
            raise ValueError("WebDAV 目录响应缺少资源地址，不能确认扫描范围")
        resourcePath = _url_from_href(path, href).rstrip("/")
        if resourcePath == normalizedPath:
            if not _is_collection(response):
                raise ValueError("WebDAV 目标不是可读取目录")
            directoryConfirmed = True
    # 空 multistatus 不代表空目录，必须有目标目录自身的成功记录。
    if not directoryConfirmed:
        raise ValueError("WebDAV 目录响应不完整，未确认目标目录自身记录")


def _prop_text(response: ET.Element, prop_name: str) -> str | None:
    for prop in _successfulProperties(response):
        text = prop.findtext(f"{{DAV:}}{prop_name}")
        if text:
            value = text.strip()
            if value:
                return value
    return None


def _entry_name_from_href(href: str) -> str:
    stripped = unquote(href).rstrip("/")
    if not stripped:
        return "/"
    return stripped.rsplit("/", 1)[-1]


def _url_from_href(base_url: str, href: str) -> str:
    # 先检查 href 本身，避免 urljoin 消除 .. 后掩盖越界输入。
    if any(ord(char) < 32 or ord(char) == 127 for char in href) or "\\" in href:
        raise ValueError("WebDAV 响应包含非法地址字符")
    parsed = urlparse(href)
    if any(part in {".", ".."} for part in unquote(parsed.path).split("/")):
        raise ValueError("WebDAV 响应包含目录跳转")
    return _confined_url(urljoin(base_url, href), base_url)


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


def isStrongEtag(value: str | None) -> bool:
    return bool(value and len(value) >= 2 and value[0] == '"' and value[-1] == '"'
                and all(character == "!" or "#" <= character <= "~" for character in value[1:-1]))


def _requestStatus(
    path: str,
    method: str,
    context: SharedPathContext,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> int:
    try:
        with _request(path, method, context, body=body, extra_headers=headers) as response:
            rawStatus = getattr(response, "status", None)
            if rawStatus is None:
                raise RuntimeError("WebDAV 响应缺少状态码")
            return int(rawStatus)
    except HTTPError as exc:
        status = int(exc.code)
        _closeHttpError(exc)
        return status


class WebDavProtocol:
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            protocol="webdav",
            display_name="WebDAV",
            supports_credentials=True,
            supports_directory_browse=True,
            supports_scan=True,
            supports_rename=True,
            requires_system_mount=False,
            can_verify_filesystem_type=False,
            future_candidate=False,
            user_notice=(
                "支持 HTTPS WebDAV 连接测试、目录浏览、递归扫描和命名预览；"
                "完成端点条件 MOVE 安全验证后才开放真实重命名、回滚和失败恢复。"
            ),
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
            if context and context.root_url:
                _confined_url(path, context.root_url)
        except ValueError as exc:
            return ConnectionTestResult(False, str(exc), "请输入类似 https://nas.example.com/dav 的地址")
        return ConnectionTestResult(True, "WebDAV 地址格式正确")

    def test_connection(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        validation = self.validate_config(path, context)
        if not validation.success:
            return validation
        try:
            root = _propfind(path, context)
            normalized = _normalize_webdav_url(path).rstrip("/")
            for item in root:
                href = _href_value(item)
                if href and _url_from_href(path, href).rstrip("/") == normalized and _is_collection(item):
                    return ConnectionTestResult(True, "WebDAV 目录读取成功；写入能力未验证", readable=True, writable=None)
            return ConnectionTestResult(False, "WebDAV 响应未确认目标为可读取目录", "请检查目录地址及 PROPFIND 权限", readable=False)
        except ValueError as exc:
            return ConnectionTestResult(False, str(exc), "请检查目录地址、读取权限及响应大小", readable=False)
        except Exception as exc:  # noqa: BLE001 - 连接测试需要转换为用户可读结果。
            return _webdav_error_message(exc)

    def list_directories(self, path: str, context: SharedPathContext | None = None) -> DirectoryListing:
        validation = self.validate_config(path, context)
        if not validation.success:
            raise ValueError(validation.message)
        root = _propfind(path, context)
        _requireDirectoryListing(root, path)
        base_url = _normalize_webdav_url(path).rstrip("/") + "/"
        base_path = urlparse(base_url).path.rstrip("/") + "/"
        entries: list[DirectoryEntry] = []
        for response in root:
            href = _href_value(response)
            if not href or not _is_collection(response):
                continue
            entryUrl = _url_from_href(base_url, href)
            href_path = urlparse(entryUrl).path
            if href_path.rstrip("/") == base_path.rstrip("/"):
                continue
            name = _entry_name_from_href(href)
            entries.append(
                DirectoryEntry(
                    name=name,
                    path=entryUrl.rstrip("/") + "/",
                    is_directory=True,
                    readable=True,
                    writable=None,
                )
            )
        entries.sort(key=lambda item: item.name.lower())
        parent = base_url.rstrip("/").rsplit("/", 1)[0] + "/" if base_url.rstrip("/") else None
        rootUrl = context.root_url if context and context.root_url else base_url
        if parent:
            try:
                parent = _confined_url(parent, rootUrl)
            except ValueError:
                parent = None
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
            _requireDirectoryListing(root, current_url)
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

    def get_file_info(self, path: str, context: SharedPathContext | None = None) -> RemoteFileEntry:
        resource = _propfind_optional(path, context)
        if resource is None:
            raise ValueError("WebDAV 源文件不存在")
        normalizedPath = _normalize_webdav_url(path)
        for response in resource:
            href = _href_value(response)
            if not href:
                continue
            resourcePath = _url_from_href(normalizedPath.rsplit("/", 1)[0] + "/", href)
            if resourcePath != normalizedPath:
                continue
            if _is_collection(response):
                raise ValueError("WebDAV 源路径不是文件")
            name = _entry_name_from_href(href)
            modifiedAt = _prop_text(response, "getlastmodified")
            return RemoteFileEntry(
                path=normalizedPath, name=name,
                extension="." + name.rsplit(".", 1)[-1].lower() if "." in name else "",
                file_size=_webdav_file_size(_prop_text(response, "getcontentlength")),
                modified_at=_webdav_modified_at(modifiedAt) if modifiedAt else "",
                version=_prop_text(response, "getetag"),
            )
        raise ValueError("WebDAV 未返回目标文件元数据")

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
        if context is None or context.conditional_move_status != "verified":
            status = context.conditional_move_status if context else "unknown"
            message = (
                "当前 WebDAV 服务不支持安全条件移动，已禁用重命名和回滚"
                if status == "unsupported"
                else "WebDAV 条件移动能力尚未验证，已禁用重命名和回滚"
            )
            return ConnectionTestResult(
                False,
                message,
                "仍可浏览、扫描和生成预览；完成端点写入安全验证后才能执行 MOVE",
                readable=True,
                writable=False,
            )

        try:
            _confined_url(target_path, _move_root(_normalize_webdav_url(source_path), context))
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
            "真实 MOVE 执行时会再次使用远程写操作锁和禁止覆盖策略",
            readable=True,
            writable=True,
        )

    def move_file(
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

        if context is None or context.conditional_move_status != "verified":
            return ConnectionTestResult(
                False,
                "服务端条件写入能力未验证，拒绝真实移动",
                "请先完成该 WebDAV 媒体源的条件 MOVE 安全验证",
            )

        expectedVersion = context.expected_source_version if context else None
        if not isStrongEtag(expectedVersion):
            return ConnectionTestResult(
                False,
                "服务端条件写入能力未验证，拒绝真实移动",
                "请重新扫描获取强 ETag，并确认该 WebDAV 服务支持条件 MOVE",
            )

        try:
            headers = {
                "Destination": _normalize_webdav_url(target_path),
                "Overwrite": "F",
                "If-Match": expectedVersion,
            }
            with _request(
                source_path,
                "MOVE",
                context,
                extra_headers=headers,
            ) as response:
                rawStatus = getattr(response, "status", None)
                if rawStatus is None:
                    return ConnectionTestResult(
                        False,
                        "WebDAV MOVE 已发出，但响应状态缺失，需要人工核查",
                        "请核对源路径和目标路径后再决定如何处理，系统不会自动重试",
                        indeterminate=True,
                    )
                status = int(rawStatus)
        except Exception as exc:  # noqa: BLE001 - WebDAV 需要转换为用户可读错误。
            return _webdav_error_message(exc)

        if status == 201:
            return ConnectionTestResult(True, "WebDAV MOVE 执行成功", readable=True, writable=True)
        if status == 204 or 200 <= status < 300:
            return ConnectionTestResult(
                False,
                f"WebDAV MOVE 返回异常成功状态 HTTP {status}，结果需要人工核查",
                "请核对源路径和目标路径后再决定如何处理，系统不会自动重试",
                indeterminate=True,
            )
        return ConnectionTestResult(False, f"WebDAV MOVE 返回 HTTP {status}", "请检查目标路径和服务端权限")

    def verify_conditional_move(
        self,
        path: str,
        context: SharedPathContext,
    ) -> WebDavCapabilityVerification:
        """在随机隔离目录内验证条件 MOVE 与禁止覆盖语义。"""

        connection = self.test_connection(path, context)
        if not connection.success:
            return WebDavCapabilityVerification(
                status="unknown",
                message=connection.message,
                suggestion=connection.suggestion,
            )

        rootUrl = _normalize_webdav_url(path).rstrip("/") + "/"
        token = secrets.token_hex(12)
        testRoot = rootUrl + f".mediaai-capability-{token}/"
        sourceUrl = testRoot + "conditional-source.bin"
        movedUrl = testRoot + "conditional-moved.bin"
        wrongTargetUrl = testRoot + "wrong-etag-target.bin"
        overwriteSourceUrl = testRoot + "overwrite-source.bin"
        overwriteTargetUrl = testRoot + "overwrite-target.bin"
        collectionCreated = False
        result: WebDavCapabilityVerification
        evidence: dict[str, int] = {}

        try:
            if _propfind_optional(testRoot, context) is not None:
                raise RuntimeError("随机验证目录已存在，拒绝使用")
            createStatus = _requestStatus(testRoot, "MKCOL", context)
            if createStatus != 201:
                raise RuntimeError(f"创建验证目录返回 HTTP {createStatus}")
            collectionCreated = True

            putStatus = _requestStatus(
                sourceUrl,
                "PUT",
                context,
                body=("mediaai-conditional-source-" + token).encode("ascii"),
                headers={"Content-Type": "application/octet-stream"},
            )
            if putStatus not in {200, 201, 204}:
                raise RuntimeError(f"创建验证文件返回 HTTP {putStatus}")
            sourceInfo = self.get_file_info(sourceUrl, context)
            if not isStrongEtag(sourceInfo.version):
                raise _UnsupportedConditionalMove("WebDAV 未为验证文件提供强 ETag")

            wrongStatus = _requestStatus(
                sourceUrl,
                "MOVE",
                context,
                headers={
                    "Destination": wrongTargetUrl,
                    "Overwrite": "F",
                    "If-Match": f'"mediaai-invalid-{token}"',
                },
            )
            evidence["wrong_etag_status"] = wrongStatus
            if wrongStatus != 412:
                raise _UnsupportedConditionalMove("WebDAV 未按 HTTP 412 拒绝错误 ETag 的 MOVE")
            sourceAfterWrong = self.get_file_info(sourceUrl, context)
            if sourceAfterWrong.version != sourceInfo.version or _propfind_optional(wrongTargetUrl, context) is not None:
                raise _UnsupportedConditionalMove("错误 ETag 的 MOVE 改变了远端文件")

            correctStatus = _requestStatus(
                sourceUrl,
                "MOVE",
                context,
                headers={
                    "Destination": movedUrl,
                    "Overwrite": "F",
                    "If-Match": sourceInfo.version,
                },
            )
            evidence["correct_etag_status"] = correctStatus
            if correctStatus != 201:
                raise _UnsupportedConditionalMove("WebDAV 未以 HTTP 201 完成正确 ETag 的 MOVE")
            if _propfind_optional(sourceUrl, context) is not None:
                raise RuntimeError("正确 ETag 的 MOVE 后源文件仍存在")
            movedInfo = self.get_file_info(movedUrl, context)
            if not isStrongEtag(movedInfo.version):
                raise RuntimeError("MOVE 后目标文件身份无法确认")

            for targetUrl, payload in (
                (overwriteSourceUrl, ("mediaai-overwrite-source-" + token).encode("ascii")),
                (overwriteTargetUrl, ("mediaai-overwrite-target-" + token).encode("ascii")),
            ):
                status = _requestStatus(
                    targetUrl,
                    "PUT",
                    context,
                    body=payload,
                    headers={"Content-Type": "application/octet-stream"},
                )
                if status not in {200, 201, 204}:
                    raise RuntimeError(f"创建禁止覆盖验证文件返回 HTTP {status}")
            overwriteSourceInfo = self.get_file_info(overwriteSourceUrl, context)
            overwriteTargetInfo = self.get_file_info(overwriteTargetUrl, context)
            if not isStrongEtag(overwriteSourceInfo.version) or not isStrongEtag(overwriteTargetInfo.version):
                raise _UnsupportedConditionalMove("WebDAV 未为禁止覆盖验证文件提供强 ETag")
            overwriteStatus = _requestStatus(
                overwriteSourceUrl,
                "MOVE",
                context,
                headers={
                    "Destination": overwriteTargetUrl,
                    "Overwrite": "F",
                    "If-Match": overwriteSourceInfo.version,
                },
            )
            evidence["overwrite_status"] = overwriteStatus
            if overwriteStatus != 412:
                raise _UnsupportedConditionalMove("WebDAV 未按 HTTP 412 保护已存在的目标文件")
            sourceAfterOverwrite = self.get_file_info(overwriteSourceUrl, context)
            targetAfterOverwrite = self.get_file_info(overwriteTargetUrl, context)
            if (
                sourceAfterOverwrite.version != overwriteSourceInfo.version
                or targetAfterOverwrite.version != overwriteTargetInfo.version
            ):
                raise _UnsupportedConditionalMove("禁止覆盖 MOVE 改变了源文件或目标文件")

            result = WebDavCapabilityVerification(
                status="verified",
                message="WebDAV 条件 MOVE 安全验证通过",
                suggestion="已允许该媒体源执行真实重命名和回滚",
                evidence=evidence,
            )
        except _UnsupportedConditionalMove as exc:
            result = WebDavCapabilityVerification(
                status="unsupported",
                message=str(exc),
                suggestion="该媒体源仍可浏览、扫描和生成预览，但不能执行真实重命名或回滚",
                evidence=evidence,
            )
        except Exception as exc:  # noqa: BLE001 - 任何不确定结果都必须保持写入门禁。
            result = WebDavCapabilityVerification(
                status="unknown",
                message="WebDAV 条件 MOVE 验证未能完成",
                suggestion=f"验证结果不明确（{type(exc).__name__}），请确认临时目录已清理后重试",
                evidence=evidence,
            )

        cleanupSucceeded = not collectionCreated
        if collectionCreated:
            try:
                deleteStatus = _requestStatus(testRoot, "DELETE", context)
                cleanupSucceeded = deleteStatus in {200, 204} and _propfind_optional(testRoot, context) is None
            except Exception:  # noqa: BLE001 - 清理失败必须覆盖能力结论。
                cleanupSucceeded = False
        if not cleanupSucceeded:
            return WebDavCapabilityVerification(
                status="unknown",
                message="WebDAV 条件 MOVE 验证目录清理失败",
                suggestion="请人工检查媒体源根目录中的 .mediaai-capability-* 临时目录，清理后再重试",
                cleanup_succeeded=False,
                evidence=evidence,
            )
        return WebDavCapabilityVerification(
            status=result.status,
            message=result.message,
            suggestion=result.suggestion,
            cleanup_succeeded=True,
            evidence=result.evidence,
        )

    def normalize_path(self, path: str) -> str:
        return _normalize_webdav_url(path)
