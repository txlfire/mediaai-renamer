"""Already-mounted NAS / NFS style paths."""

from pathlib import Path

from app.service.shared_protocols.base import (
    ConnectionTestResult,
    DirectoryListing,
    ProtocolCapabilities,
    SharedPathContext,
)
from app.service.shared_protocols.local import _directory_listing, _test_local_directory, _test_local_rename


def _mount_info_for_path(path: Path) -> tuple[str | None, str | None]:
    mounts_path = Path("/proc/mounts")
    if not mounts_path.exists():
        return None, "无法确认文件系统类型"

    try:
        target = path.expanduser().resolve()
        best_mount: Path | None = None
        best_type: str | None = None
        for line in mounts_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mount_point = Path(parts[1].replace("\\040", " "))
            try:
                resolved_mount = mount_point.resolve()
                target.relative_to(resolved_mount)
            except (OSError, ValueError):
                continue
            if best_mount is None or len(str(resolved_mount)) > len(str(best_mount)):
                best_mount = resolved_mount
                best_type = parts[2]
        if best_type is None:
            return None, "无法确认文件系统类型"
        return best_type, None
    except OSError:
        return None, "无法确认文件系统类型"


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

    def validate_config(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        return _test_local_directory(path)

    def test_connection(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        result = _test_local_directory(path)
        if not result.success:
            return result

        filesystem_type, filesystem_warning = _mount_info_for_path(Path(path))
        suggestion = result.suggestion
        if filesystem_warning:
            suggestion = filesystem_warning
        elif filesystem_type not in {"nfs", "nfs4"}:
            suggestion = f"当前文件系统类型为 {filesystem_type}，请确认它是服务端已挂载的共享路径"

        return ConnectionTestResult(
            True,
            "连接成功",
            suggestion,
            readable=result.readable,
            writable=result.writable,
            filesystem_type=filesystem_type,
        )

    def list_directories(self, path: str, context: SharedPathContext | None = None) -> DirectoryListing:
        result = self.test_connection(path, context)
        if not result.success:
            raise ValueError(result.message)
        return _directory_listing(path)

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
        return str(Path(path).expanduser().resolve())
