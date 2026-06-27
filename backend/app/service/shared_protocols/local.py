"""Local filesystem protocol."""

from pathlib import Path

from app.service.shared_protocols.base import (
    ConnectionTestResult,
    DirectoryEntry,
    DirectoryListing,
    ProtocolCapabilities,
)


def _directory_listing(path: str) -> DirectoryListing:
    current_path = Path(path).expanduser().resolve()
    entries = [
        DirectoryEntry(name=item.name, path=str(item), is_directory=True)
        for item in current_path.iterdir()
        if item.is_dir()
    ]
    entries.sort(key=lambda item: item.name.lower())
    parent_path = str(current_path.parent) if current_path.parent != current_path else None
    return DirectoryListing(
        current_path=str(current_path),
        parent_path=parent_path,
        entries=entries,
    )


def _test_local_directory(path: str) -> ConnectionTestResult:
    if not path.strip():
        return ConnectionTestResult(False, "路径不能为空", "请输入服务可访问的目录路径")
    current_path = Path(path).expanduser()
    if not current_path.exists():
        return ConnectionTestResult(False, "目录不存在", "请检查路径是否存在，或确认挂载已完成")
    if not current_path.is_dir():
        return ConnectionTestResult(False, "路径不是目录", "请选择目录路径")
    return ConnectionTestResult(True, "连接成功")


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

    def test_connection(self, path: str) -> ConnectionTestResult:
        return _test_local_directory(path)

    def list_directories(self, path: str) -> DirectoryListing:
        result = _test_local_directory(path)
        if not result.success:
            raise ValueError(result.message)
        return _directory_listing(path)
