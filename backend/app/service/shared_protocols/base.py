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


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    path: str
    is_directory: bool


@dataclass(frozen=True)
class DirectoryListing:
    current_path: str | None
    parent_path: str | None
    entries: list[DirectoryEntry]


class SharedProtocol(Protocol):
    def capabilities(self) -> ProtocolCapabilities:
        ...

    def test_connection(self, path: str) -> ConnectionTestResult:
        ...

    def list_directories(self, path: str) -> DirectoryListing:
        ...
