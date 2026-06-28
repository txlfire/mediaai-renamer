"""Local filesystem protocol."""

import os
from pathlib import Path

from app.service.shared_protocols.base import (
    ConnectionTestResult,
    DirectoryEntry,
    DirectoryListing,
    ProtocolCapabilities,
    SharedPathContext,
)


def _directory_listing(path: str) -> DirectoryListing:
    current_path = Path(path).expanduser().resolve()
    entries = [
        DirectoryEntry(
            name=item.name,
            path=str(item),
            is_directory=True,
            readable=os.access(item, os.R_OK),
            writable=os.access(item, os.W_OK),
        )
        for item in current_path.iterdir()
        if item.is_dir()
    ]
    entries.sort(key=lambda item: item.name.lower())
    parent_path = str(current_path.parent) if current_path.parent != current_path else None
    return DirectoryListing(current_path=str(current_path), parent_path=parent_path, entries=entries)


def _test_local_directory(path: str) -> ConnectionTestResult:
    if not path.strip():
        return ConnectionTestResult(False, "路径不能为空", "请输入服务端可访问的目录路径")
    current_path = Path(path).expanduser()
    try:
        if not current_path.exists():
            return ConnectionTestResult(False, "目录不存在", "请检查路径是否存在，或确认挂载已经完成")
        if not current_path.is_dir():
            return ConnectionTestResult(False, "路径不是目录", "请选择目录路径")
        readable = os.access(current_path, os.R_OK)
        writable = os.access(current_path, os.W_OK)
    except OSError as exc:
        return ConnectionTestResult(
            False,
            "目录不可访问",
            f"请检查网络连接、挂载状态或服务运行用户权限：{exc}",
        )
    if not readable:
        return ConnectionTestResult(
            False,
            "目录不可读",
            "请检查服务运行用户是否具有读取权限",
            readable=readable,
            writable=writable,
        )
    if not writable:
        return ConnectionTestResult(
            False,
            "目录不可写",
            "请检查服务运行用户是否具有写入权限",
            readable=readable,
            writable=writable,
        )
    return ConnectionTestResult(True, "连接成功", readable=readable, writable=writable)


def _test_local_rename(source_path: str, target_path: str) -> ConnectionTestResult:
    source = Path(source_path).expanduser()
    target_parent = Path(target_path).expanduser().parent
    try:
        if not source.exists():
            return ConnectionTestResult(False, "源文件不存在", "请先重新扫描，确认源文件仍然存在")
        if not source.is_file():
            return ConnectionTestResult(False, "源路径不是文件", "请检查命名预览数据")
        if not target_parent.exists():
            return ConnectionTestResult(False, "目标目录不存在", "请检查媒体源路径或重新生成预览")
        if not target_parent.is_dir():
            return ConnectionTestResult(False, "目标父级路径不是目录", "请检查目标路径")
        if not os.access(target_parent, os.W_OK):
            return ConnectionTestResult(False, "目标目录不可写", "请检查服务运行用户是否具有写入权限")
    except OSError as exc:
        return ConnectionTestResult(
            False,
            "文件操作校验失败",
            f"请检查网络连接、挂载状态或服务运行用户权限：{exc}",
        )
    return ConnectionTestResult(True, "文件操作校验通过")


class LocalProtocol:
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            protocol="local",
            display_name="本地路径",
            supports_credentials=False,
            supports_directory_browse=True,
            supports_scan=True,
            supports_rename=True,
            requires_system_mount=False,
            can_verify_filesystem_type=False,
            future_candidate=False,
            user_notice="服务所在机器必须能访问该路径。",
        )

    def validate_config(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        return _test_local_directory(path)

    def test_connection(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        return _test_local_directory(path)

    def list_directories(self, path: str, context: SharedPathContext | None = None) -> DirectoryListing:
        result = _test_local_directory(path)
        if not result.success:
            raise ValueError(result.message)
        return _directory_listing(path)

    def check_scan_ready(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        return _test_local_directory(path)

    def check_rename_ready(
        self,
        source_path: str,
        target_path: str,
        context: SharedPathContext | None = None,
    ) -> ConnectionTestResult:
        return _test_local_rename(source_path, target_path)

    def normalize_path(self, path: str) -> str:
        return str(Path(path).expanduser().resolve())
