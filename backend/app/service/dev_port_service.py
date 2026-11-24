"""本地开发端口的启动前读取、校验与审计。"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import socket
import sqlite3

from app.core.config import AppSettings
from app.service.audit_service import AuditWriteContext, record_audit_event


DEV_PORTS_KEY = "server.local_dev_ports"
# 避开特权端口；未配置时兼容既有 Vite 和 FastAPI 开发入口。
MIN_DEV_PORT = 1024
MAX_DEV_PORT = 65535
DEFAULT_FRONTEND_PORT = 5173
DEFAULT_BACKEND_PORT = 8970


class DevPortError(Exception):
    """对外仅返回稳定错误码，不暴露主机或存储细节。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PortPair:
    frontendPort: int
    backendPort: int

    def __post_init__(self):
        if any(type(port) is not int or not MIN_DEV_PORT <= port <= MAX_DEV_PORT
               for port in (self.frontendPort, self.backendPort)) or self.frontendPort == self.backendPort:
            raise DevPortError("invalid_service_ports")

    def payload(self):
        return {"frontendPort": self.frontendPort, "backendPort": self.backendPort}


DEFAULT_DEV_PORTS = PortPair(DEFAULT_FRONTEND_PORT, DEFAULT_BACKEND_PORT)


def _readStored(connection: sqlite3.Connection, *, allowMissingTable: bool = False):
    if allowMissingTable and not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_settings'"
    ).fetchone():
        return None, 0
    row = connection.execute("SELECT value FROM system_settings WHERE key = ?", (DEV_PORTS_KEY,)).fetchone()
    if row is None:
        return None, 0
    try:
        values = json.loads(row[0])
        revision = values["revision"]
        if type(revision) is not int or not 1 <= revision < 2**53 - 1:
            raise ValueError
        return PortPair(values["frontendPort"], values["backendPort"]), revision
    except (TypeError, ValueError, KeyError, DevPortError):
        raise DevPortError("service_ports_storage_failed") from None


def readSavedDevPorts(settings: AppSettings):
    path = settings.database_path.resolve()
    if not path.exists():
        return None, 0
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as connection:
            return _readStored(connection, allowMissingTable=True)
    except sqlite3.Error:
        raise DevPortError("service_ports_storage_failed") from None


def resolveDevPorts(settings: AppSettings, frontendPort: int | None = None, backendPort: int | None = None):
    # 两项明确覆盖时不读取旧设置，保留损坏配置的入口回退办法。
    if frontendPort is not None and backendPort is not None:
        return PortPair(frontendPort, backendPort)
    persisted, _ = readSavedDevPorts(settings)
    fallback = persisted or DEFAULT_DEV_PORTS
    return PortPair(frontendPort if frontendPort is not None else fallback.frontendPort,
                    backendPort if backendPort is not None else fallback.backendPort)


def activeDevPorts():
    # 由启动进程传入实际监听端口，不根据代理头、URL 或数据库推断运行模式。
    frontend = os.environ.get("MEDIAAI_DEV_FRONTEND_PORT")
    backend = os.environ.get("MEDIAAI_DEV_BACKEND_PORT")
    if frontend is None and backend is None:
        return None
    try:
        return PortPair(int(frontend or ""), int(backend or ""))
    except (ValueError, DevPortError):
        raise DevPortError("service_ports_runtime_unavailable") from None


def checkDevPortsAvailable(ports: PortPair, active: PortPair | None = None):
    activePorts = set(active.payload().values()) if active else set()
    # 监听范围由启动环境决定；回环验证不在局域网临时监听。
    host = "127.0.0.1" if os.environ.get("MEDIAAI_DEV_LOOPBACK_ONLY") == "1" else "0.0.0.0"
    for name, port in ports.payload().items():
        if port in activePorts:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                if os.name == "nt":
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((host, port))
                listener.listen(1)
        except OSError:
            code = "frontend_port_unavailable" if name == "frontendPort" else "backend_port_unavailable"
            raise DevPortError(code) from None


def _snapshot(active: PortPair, desired: PortPair, revision: int):
    return {"mode": "local-dev", "active": active.payload(), "desired": desired.payload(),
            "revision": revision, "restartRequired": desired != active}


def readDevPorts(settings: AppSettings):
    active = activeDevPorts()
    if active is None:
        return {"mode": "deployment"}
    desired, revision = readSavedDevPorts(settings)
    return _snapshot(active, desired or active, revision)


def saveDevPorts(settings: AppSettings, ports: PortPair, expectedRevision: int, audit: AuditWriteContext):
    active = activeDevPorts()
    if active is None:
        raise DevPortError("service_ports_deployment_managed")
    checkDevPortsAvailable(ports, active)
    try:
        with closing(sqlite3.connect(settings.database_path, timeout=0.5, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous, revision = _readStored(connection)
            if expectedRevision != revision:
                raise DevPortError("service_ports_changed")
            if ports == previous:
                return _snapshot(active, ports, revision)
            if revision >= 2**53 - 2:
                raise DevPortError("service_ports_revision_exhausted")
            revision += 1
            now = datetime.now(timezone.utc).isoformat()
            value = json.dumps({**ports.payload(), "revision": revision})
            connection.execute(
                "INSERT INTO system_settings (key, category, value, description, operator, created_at, updated_at) "
                "VALUES (?, 'server', ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, operator=excluded.operator, updated_at=excluded.updated_at",
                (DEV_PORTS_KEY, value, "本地开发端口，下次手动启动生效",
                 audit.actor.username if audit.actor else "system", now, now),
            )
            record_audit_event(settings, event_type="settings.update", action="save_dev_ports", result="success",
                               summary="保存本地开发端口，未改变当前监听", target_type="settings",
                               target_id=DEV_PORTS_KEY, actor=audit.actor, ip_address=audit.ipAddress,
                               user_agent=audit.userAgent, detail={**ports.payload(), "revision": revision},
                               connection=connection)
            connection.commit()
            return _snapshot(active, ports, revision)
    except sqlite3.Error:
        raise DevPortError("service_ports_storage_failed") from None
