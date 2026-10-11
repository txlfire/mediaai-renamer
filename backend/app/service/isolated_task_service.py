"""扫描和文件任务共用的进程监督；实际退出前不释放持久化槽位。"""

import asyncio
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

from app.core.logger import get_logger
from app.service.settings_service import resolveRuntimeLogging


class TaskProcessStartError(RuntimeError):
    """启动交接失败时仍将已创建进程交回监督者回收。"""

    def __init__(self, process):
        super().__init__("后台进程启动交接失败")
        self.process = process


def startTaskProcess(settings, module: str, payload: dict, persistPid: Callable,
                     errorType=TaskProcessStartError):
    logging = resolveRuntimeLogging(settings)
    payload = {
        "database_path": str(settings.database_path.resolve()), "data_dir": str(settings.data_dir.resolve()),
        "credential_key_file": str(settings.credential_key_file.resolve()) if settings.credential_key_file else None,
        "log_dir": str(logging.log_dir.resolve()), "log_level": logging.level,
        "log_max_size_mb": logging.max_size_mb, "log_backup_count": logging.backup_count,
        "external_requests": asdict(settings.external_requests),
        **payload,
    }
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    process = subprocess.Popen([sys.executable, "-m", module], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        # 子进程在stdin读取前不会接触媒体；先可靠保存PID再授权执行。
        persistPid(process.pid)
        process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        process.stdin.close()
    except Exception as exc:
        if not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        raise errorType(process) from exc
    return process


@dataclass(frozen=True)
class TaskProcessHooks:
    label: str
    recover: Callable
    claim: Callable
    start: Callable
    timeout: Callable
    heartbeat: Callable
    finish: Callable


async def _pause(stop: asyncio.Event, seconds: float = 1) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def runTaskProcessLoop(settings, stop: asyncio.Event, hooks: TaskProcessHooks) -> None:
    logger = get_logger(__name__)
    while not stop.is_set():
        process = None
        claim = None
        reason = "后台执行进程已结束，未完成任务已标记为中断；请核对文件动作，不会自动重试"
        try:
            await asyncio.to_thread(hooks.recover, settings)
            claim = await asyncio.to_thread(hooks.claim, settings)
            if claim is None:
                await _pause(stop)
                continue
            taskId, token = claim
            process = await asyncio.to_thread(hooks.start, settings, taskId, token)
            deadline = time.monotonic() + await asyncio.to_thread(hooks.timeout, settings, taskId)
            nextHeartbeat = time.monotonic()
            while process.poll() is None:
                if stop.is_set() or time.monotonic() >= deadline:
                    reason = (f"服务停止，{hooks.label}已中断" if stop.is_set() else
                              f"{hooks.label}超过任务总时间上限，已中断；不会自动重试")
                    break
                if time.monotonic() >= nextHeartbeat:
                    if not await asyncio.to_thread(hooks.heartbeat, settings, taskId, token):
                        reason = f"{hooks.label}执行授权失效，后台进程停止；请核对已发生动作"
                        break
                    nextHeartbeat = time.monotonic() + 5
                await _pause(stop)
        except Exception as exc:  # noqa: BLE001 - 不记录配置/原始异常，不重放任务。
            if isinstance(exc, TaskProcessStartError):
                process = exc.process
            logger.error("%s后台调度失败 error_type=%s", hooks.label, type(exc).__name__)
        finally:
            if claim is not None:
                taskId, token = claim
                if process is not None and process.poll() is None:
                    try:
                        await asyncio.to_thread(hooks.finish, settings, taskId, token, reason, processExited=False)
                    except Exception as exc:
                        logger.error("%s中断意图保存失败 error_type=%s", hooks.label, type(exc).__name__)
                    try:
                        process.kill()
                    except OSError as exc:
                        logger.error("%s进程终止失败 error_type=%s", hooks.label, type(exc).__name__)
                    # NFS不可中断IO可能延迟退出；不能仅凭kill成功释放槽位。
                    while process.poll() is None:
                        await asyncio.sleep(0.2)
                if process is not None:
                    process.wait()
                try:
                    await asyncio.to_thread(hooks.finish, settings, taskId, token, reason, processExited=True)
                except Exception as exc:
                    logger.error("%s终态保存失败 error_type=%s", hooks.label, type(exc).__name__)
        await _pause(stop)
