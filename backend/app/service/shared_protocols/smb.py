"""SMB / Windows UNC protocol."""

from pathlib import Path

from app.service.shared_protocols.base import (
    ConnectionTestResult,
    DirectoryListing,
    ProtocolCapabilities,
    SharedPathContext,
)
from app.service.shared_protocols.local import _directory_listing, _test_local_directory, _test_local_rename


def _is_unc_path(path: str) -> bool:
    parts = [part for part in path.strip().split("\\") if part]
    return path.strip().startswith("\\\\") and len(parts) >= 2


def _unc_path_parts(path: str) -> tuple[str, str] | None:
    if not _is_unc_path(path):
        return None
    parts = [part for part in path.strip().split("\\") if part]
    return parts[0], parts[1]


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

    def validate_config(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        if not _is_unc_path(path):
            return ConnectionTestResult(False, "UNC 路径格式不正确", r"请输入类似 \\server\share 的路径")
        return ConnectionTestResult(True, "UNC 路径格式正确")

    def test_connection(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        if not _is_unc_path(path):
            return ConnectionTestResult(False, "UNC 路径格式不正确", r"请输入类似 \\server\share 的路径")

        parts = _unc_path_parts(path)
        if context is not None and parts is not None:
            host, share_name = parts
            if context.host and context.host != host:
                return ConnectionTestResult(False, "UNC 主机与媒体源配置不一致", "请检查 SMB 主机字段或目标路径")
            if context.share_name and context.share_name != share_name:
                return ConnectionTestResult(False, "UNC 共享名与媒体源配置不一致", "请检查共享名字段或目标路径")

        result = _test_local_directory(path)
        if not result.success:
            suggestion = result.suggestion or "请确认服务所在 Windows 主机已登录共享、网络可达，并且账号具备访问权限"
            if context is not None and context.username and not context.has_secret:
                suggestion = "已填写 SMB 用户名但未填写密码；请补充密码后重试，或确认系统已使用该账号登录共享"
            return ConnectionTestResult(
                False,
                result.message,
                suggestion,
                readable=result.readable,
                writable=result.writable,
            )

        return ConnectionTestResult(
            True,
            "连接成功",
            "共享路径可访问，当前服务进程具备读写权限",
            readable=result.readable,
            writable=result.writable,
        )

    def list_directories(self, path: str, context: SharedPathContext | None = None) -> DirectoryListing:
        result = self.test_connection(path, context)
        if not result.success:
            raise ValueError(result.message)
        listing = _directory_listing(path)
        return DirectoryListing(
            current_path=path,
            parent_path=str(Path(path).parent) if str(Path(path).parent) != path else None,
            entries=listing.entries,
        )

    def check_scan_ready(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        return self.test_connection(path, context)

    def check_rename_ready(
        self,
        source_path: str,
        target_path: str,
        context: SharedPathContext | None = None,
    ) -> ConnectionTestResult:
        result = self.test_connection(str(Path(source_path).parent), context)
        if not result.success:
            return result
        return _test_local_rename(source_path, target_path)

    def normalize_path(self, path: str) -> str:
        return path.strip()
