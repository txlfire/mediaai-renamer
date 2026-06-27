"""Already-mounted NAS / NFS style paths."""

from app.service.shared_protocols.base import ConnectionTestResult, DirectoryListing, ProtocolCapabilities
from app.service.shared_protocols.local import _directory_listing, _test_local_directory


class MountedNfsProtocol:
    def capabilities(self) -> ProtocolCapabilities:
        return ProtocolCapabilities(
            protocol="mounted_nfs",
            display_name="已挂载路径 / NFS",
            supports_credentials=False,
            supports_directory_browse=True,
            supports_scan=True,
            supports_rename=True,
            requires_system_mount=True,
            can_verify_filesystem_type=True,
            future_candidate=False,
            user_notice="不保存账号密码；请先在系统或宿主机完成挂载并确保服务可访问。",
        )

    def test_connection(self, path: str) -> ConnectionTestResult:
        return _test_local_directory(path)

    def list_directories(self, path: str) -> DirectoryListing:
        result = _test_local_directory(path)
        if not result.success:
            raise ValueError(result.message)
        return _directory_listing(path)
