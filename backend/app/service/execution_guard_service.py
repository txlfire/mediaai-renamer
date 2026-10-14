"""隔离任务的发送/发布守卫；普通同步调用保持原契约。"""

from contextlib import contextmanager
from contextvars import ContextVar

_guard = ContextVar("execution_guard", default=None)


def checkExecutionGuard(connection=None, *, phase="check"):
    guard = _guard.get()
    if guard is not None:
        guard(connection, phase)


@contextmanager
def executionGuard(guard):
    token = _guard.set(guard)
    try:
        yield
    finally:
        _guard.reset(token)
