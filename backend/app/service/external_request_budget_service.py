"""同一实例逐HTTP请求共享速率/实际并发；不存URL、正文或凭据。"""

from contextlib import closing, contextmanager
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import os
import math
import secrets
import sqlite3
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from app.core.logger import get_logger

_deadline = ContextVar("external_transport_deadline", default=None)


def isProcessAlive(pid):
    # 延迟加载既有本机核对，避免预算基础模块反向导入设置/审计形成循环。
    from app.service.scan_recovery_service import isProcessAlive as check
    return check(pid)


class ExternalBudgetWaitError(TimeoutError):
    """等待截止前未取得发送资格，不表示已向服务发送请求。"""


class ExternalBudgetUnavailable(OSError):
    """预算状态不可用时拒绝外发，不回显数据库位置或原始错误。"""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def urlopen(request, *, timeout):
    """每个已授权请求不自动跟随重定向，避免额外HTTP绕过预算及认证边界。"""
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


@contextmanager
def transportDeadline(deadline):
    parent = _deadline.get()
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def clientRequestDeadline(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        if self.requestBudget is None:
            return function(self, *args, **kwargs)
        with transportDeadline(time.monotonic() + self.timeout_seconds):
            return function(self, *args, **kwargs)
    return wrapped


def _siteKey(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return sha256(f"{parsed.scheme.lower()}://{host}:{port}".encode()).hexdigest()
    except (ValueError, UnicodeError):
        raise ValueError("外部服务地址无效，不发送请求") from None


def _diagnostic(service, stage, *, elapsedMs=None, error=None):
    try:
        get_logger(__name__).warning("外部请求预算 service=%s stage=%s elapsed_ms=%s error_type=%s",
            service, stage, elapsedMs, type(error).__name__ if error is not None else None)
    except Exception:  # noqa: BLE001 - 辅助诊断不覆盖请求结果。
        pass


class _Response:
    def __init__(self, response, deadline):
        self.response = response
        self.deadline = deadline

    def read(self, *args, **kwargs):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("外部请求总时间预算已耗尽，结果可能未知")
        result = self.response.read(*args, **kwargs)
        if time.monotonic() >= self.deadline:
            raise TimeoutError("外部请求总时间预算已耗尽，结果可能未知")
        return result

    def __getattr__(self, name):
        return getattr(self.response, name)


class ExternalRequestBudget:
    def __init__(self, settings, service):
        self.settings = settings
        self.service = service
        self.policy = settings.external_requests.forService(service)

    def _connect(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExternalBudgetWaitError("外部请求等待预算已耗尽，本次未发送请求")
        return sqlite3.connect(self.settings.database_path.resolve().as_uri() + "?mode=rw",
                               uri=True, timeout=min(0.05, remaining))

    def _take(self, siteKey, deadline):
        with closing(self._connect(deadline)) as connection:
            # PID核对在写事务外；随机token不会复用，删除只针对已经确认退出的原槽。
            observed = connection.execute("SELECT token,pid FROM external_request_leases LIMIT 65").fetchall()
            if len(observed) > 64:
                raise ExternalBudgetUnavailable("外部请求并发记录超过安全上限，本次未发送请求")
            dead = []
            for token, pid in observed:
                try:
                    if not isProcessAlive(pid):
                        dead.append(token)
                except (ValueError, TypeError, OSError):
                    pass
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany("DELETE FROM external_request_leases WHERE token=?", ((token,) for token in dead))
            active = connection.execute("SELECT site_key,global_limit,site_limit FROM external_request_leases LIMIT 65").fetchall()
            if any(not isinstance(limit, int) or not 1 <= limit <= 64 for row in active for limit in row[1:]):
                raise ExternalBudgetUnavailable("外部请求并发状态无效，本次未发送请求")
            globalLimit = min([self.settings.external_requests.max_concurrency, *[row[1] for row in active]])
            sameSite = [row for row in active if row[0] == siteKey]
            siteLimit = min([self.policy.max_concurrency, *[row[2] for row in sameSite]])
            if len(active) >= globalLimit or len(sameSite) >= siteLimit:
                connection.commit()
                return None
            wallNow = time.time()
            previous = connection.execute("SELECT last_started_at,spacing_seconds FROM external_request_sites WHERE site_key=?",
                                          (siteKey,)).fetchone()
            if previous is not None and any(not isinstance(value, (int, float)) or not math.isfinite(value)
                                           for value in previous):
                raise ExternalBudgetUnavailable("外部请求速率状态无效，本次未发送请求")
            if previous is not None and previous[1] <= 0:
                raise ExternalBudgetUnavailable("外部请求速率状态无效，本次未发送请求")
            spacing = 1 / self.policy.requests_per_second
            if previous is not None and wallNow < previous[0] + max(spacing, previous[1]):
                connection.commit()
                return None
            if time.monotonic() >= deadline:
                raise ExternalBudgetWaitError("外部请求等待预算已耗尽，本次未发送请求")
            # origin变化不会无限增长；清理只删超过一天且没有实际请求占槽的速率状态。
            connection.execute("DELETE FROM external_request_sites WHERE site_key IN (SELECT site.site_key "
                "FROM external_request_sites site WHERE updated_at<? AND NOT EXISTS "
                "(SELECT 1 FROM external_request_leases lease WHERE lease.site_key=site.site_key) "
                "ORDER BY updated_at LIMIT 100)", (wallNow - 86400,))
            if previous is None and connection.execute("SELECT COUNT(*) FROM external_request_sites").fetchone()[0] >= 1024:
                raise ExternalBudgetUnavailable("外部请求站点状态已满，本次未发送请求")
            token = secrets.token_urlsafe(24)
            connection.execute("INSERT INTO external_request_sites(site_key,last_started_at,spacing_seconds,updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(site_key) DO UPDATE SET last_started_at=excluded.last_started_at,"
                "spacing_seconds=excluded.spacing_seconds,updated_at=excluded.updated_at", (siteKey, wallNow, spacing, wallNow))
            connection.execute("INSERT INTO external_request_leases(token,site_key,pid,global_limit,site_limit,started_at) "
                "VALUES (?,?,?,?,?,?)", (token, siteKey, os.getpid(), self.settings.external_requests.max_concurrency,
                                      self.policy.max_concurrency, wallNow))
            connection.commit()
            return token

    def acquire(self, siteKey, deadline):
        started = time.monotonic()
        while time.monotonic() < deadline:
            try:
                token = self._take(siteKey, deadline)
            except ExternalBudgetUnavailable as exc:
                _diagnostic(self.service, "admission_rejected", error=exc)
                raise
            except sqlite3.Error as exc:
                if getattr(exc, "sqlite_errorcode", 0) & 255 not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    _diagnostic(self.service, "storage_unavailable", error=exc)
                    raise ExternalBudgetUnavailable("外部请求预算存储不可用，本次未发送请求") from None
                token = None
            if token is not None:
                elapsed = round((time.monotonic() - started) * 1000)
                if elapsed >= 50:
                    _diagnostic(self.service, "admitted_after_wait", elapsedMs=elapsed)
                return token
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        _diagnostic(self.service, "wait_timeout", elapsedMs=round((time.monotonic() - started) * 1000))
        raise ExternalBudgetWaitError("外部请求等待预算已耗尽，本次未发送请求")

    def release(self, token):
        try:
            with closing(sqlite3.connect(self.settings.database_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.05)) as connection:
                connection.execute("DELETE FROM external_request_leases WHERE token=? AND pid=?", (token, os.getpid()))
                connection.commit()
        except (sqlite3.Error, OSError) as exc:
            # 响应已经结束时不伪报业务失败；遗留槽保留，进程实际退出后才清除。
            _diagnostic(self.service, "release_failed_slot_retained", error=exc)

    @contextmanager
    def open(self, request, timeout):
        deadline = time.monotonic() + timeout
        if _deadline.get() is not None:
            deadline = min(deadline, _deadline.get())
        token = self.acquire(_siteKey(request.full_url), deadline)
        releaseSlot = True
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExternalBudgetWaitError("外部请求等待预算已耗尽，本次未发送请求")
            try:
                context = urlopen(request, timeout=remaining)
            except HTTPError as exc:
                try:
                    exc.close()
                except Exception as closeError:  # noqa: BLE001 - 关闭未确认，保留实际请求槽。
                    releaseSlot = False
                    _diagnostic(self.service, "close_failed_slot_retained", error=closeError)
                raise
            try:
                response = context.__enter__()
            except Exception as exc:  # noqa: BLE001 - 上下文未完成交接，不能假定响应资源已关闭。
                releaseSlot = False
                _diagnostic(self.service, "enter_failed_slot_retained", error=exc)
                raise
            try:
                yield _Response(response, deadline)
            finally:
                try:
                    context.__exit__(*sys.exc_info())
                except Exception as exc:  # noqa: BLE001 - 不把关闭失败视为请求实际已结束。
                    releaseSlot = False
                    _diagnostic(self.service, "exit_failed_slot_retained", error=exc)
                    raise
        finally:
            if releaseSlot:
                self.release(token)


def openExternalRequest(budget, request, timeout, legacyOpener):
    """旧独立客户端保留可注入transport；实例中的真实请求必须走共享预算。"""
    return legacyOpener(request, timeout=timeout) if budget is None else budget.open(request, timeout)
