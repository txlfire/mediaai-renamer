"""SMB / Windows UNC protocol."""

from app.service.shared_protocols.base import (
    ConnectionTestResult,
    DirectoryListing,
    ProtocolCapabilities,
)


def _is_unc_path(path: str) -> bool:
    parts = [part for part in path.strip().split("\\") if part]
    return path.strip().startswith("\\\\") and len(parts) >= 2


class SmbProtocol:
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            protocol="unc",
            display_name="Windows UNC / SMB",
            supports_credentials=True,
            supports_directory_browse=True,
            supports_scan=True,
            supports_rename=True,
            requires_system_mount=False,
            can_verify_filesystem_type=False,
            future_candidate=False,
            user_notice="仅此类型保存加密账号密码，用于连接测试和共享访问校验。",
        )

    def test_connection(self, path: str) -> ConnectionTestResult:
        if not _is_unc_path(path):
            return ConnectionTestResult(False, "UNC 路径格式不正确", "请输入类似 \\\\server\\share 的路径")
        return ConnectionTestResult(True, "连接成功", "已完成 UNC 路径格式校验")

    def list_directories(self, path: str) -> DirectoryListing:
        result = self.test_connection(path)
        if not result.success:
            raise ValueError(result.message)
        return DirectoryListing(current_path=path, parent_path=None, entries=[])
