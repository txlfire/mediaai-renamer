"""Shared path protocol interfaces and capability models."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProtocolCapabilities:
    protocol: str
    display_name: str
    supports_credentials: bool
    supports_directory_browse: bool
    supports_scan: bool
    supports_rename: bool
    requires_system_mount: bool
    can_verify_filesystem_type: bool
    future_candidate: bool
    user_notice: str


@dataclass(frozen=True)
class ConnectionTestResult:
    success: bool
    message: str
    suggestion: str | None = None
    readable: bool | None = None
    writable: bool | None = None
    filesystem_type: str | None = None


@dataclass(frozen=True)
class SharedPathContext:
    path_type: str
    username: str | None = None
    has_secret: bool = False
    host: str | None = None
    share_name: str | None = None
    domain: str | None = None
    port: int | None = None
    nfs_host: str | None = None
    nfs_export: str | None = None
    nfs_version: str | None = None
    nfs_options: str | None = None
    local_mount_path: str | None = None
    connection_timeout_seconds: int = 5
    nfs_operation_timeout_seconds: int = 30


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    path: str
    is_directory: bool
    readable: bool | None = None
    writable: bool | None = None


@dataclass(frozen=True)
class DirectoryListing:
    current_path: str | None
    parent_path: str | None
    entries: list[DirectoryEntry]


class SharedProtocol(Protocol):
    def capabilities(self) -> ProtocolCapabilities:
        ...

    def validate_config(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        ...

    def test_connection(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        ...

    def list_directories(self, path: str, context: SharedPathContext | None = None) -> DirectoryListing:
        ...

    def check_scan_ready(self, path: str, context: SharedPathContext | None = None) -> ConnectionTestResult:
        ...

    def check_rename_ready(
        self,
        source_path: str,
        target_path: str,
        context: SharedPathContext | None = None,
    ) -> ConnectionTestResult:
        ...

    def normalize_path(self, path: str) -> str:
        ...
