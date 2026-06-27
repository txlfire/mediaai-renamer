"""Shared protocol registry."""

from app.service.shared_protocols.base import ProtocolCapabilities, SharedProtocol
from app.service.shared_protocols.local import LocalProtocol
from app.service.shared_protocols.mounted_nfs import MountedNfsProtocol
from app.service.shared_protocols.smb import SmbProtocol


_PROTOCOLS: dict[str, SharedProtocol] = {
    "local": LocalProtocol(),
    "unc": SmbProtocol(),
    "mounted_nfs": MountedNfsProtocol(),
}


_FUTURE_PROTOCOLS = [
    ProtocolCapabilities(
        protocol="webdav",
        display_name="WebDAV",
        supports_credentials=True,
        supports_directory_browse=False,
        supports_scan=False,
        supports_rename=False,
        requires_system_mount=False,
        can_verify_filesystem_type=False,
        future_candidate=True,
        user_notice="当前版本暂不支持 WebDAV，后续需单独设计远程写入和锁语义。",
    ),
    ProtocolCapabilities(
        protocol="ftp",
        display_name="FTP",
        supports_credentials=True,
        supports_directory_browse=False,
        supports_scan=False,
        supports_rename=False,
        requires_system_mount=False,
        can_verify_filesystem_type=False,
        future_candidate=True,
        user_notice="当前版本暂不支持 FTP，后续需单独设计远程移动和失败恢复。",
    ),
    ProtocolCapabilities(
        protocol="sftp",
        display_name="SFTP",
        supports_credentials=True,
        supports_directory_browse=False,
        supports_scan=False,
        supports_rename=False,
        requires_system_mount=False,
        can_verify_filesystem_type=False,
        future_candidate=True,
        user_notice="当前版本暂不支持 SFTP，后续需单独设计密钥凭据和远程操作语义。",
    ),
    ProtocolCapabilities(
        protocol="s3",
        display_name="S3 / 对象存储",
        supports_credentials=True,
        supports_directory_browse=False,
        supports_scan=False,
        supports_rename=False,
        requires_system_mount=False,
        can_verify_filesystem_type=False,
        future_candidate=True,
        user_notice="当前版本暂不支持对象存储，S3 不具备本地文件系统的原子重命名语义。",
    ),
]


def get_protocol(path_type: str) -> SharedProtocol:
    try:
        return _PROTOCOLS[path_type]
    except KeyError as exc:
        raise ValueError("不支持的路径类型") from exc


def list_protocol_capabilities() -> list[ProtocolCapabilities]:
    return [item.capabilities() for item in _PROTOCOLS.values()] + _FUTURE_PROTOCOLS
