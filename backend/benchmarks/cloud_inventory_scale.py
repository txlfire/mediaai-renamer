"""独立临时库的盘点落库/分页基准；无配置入口、无网络、无云凭据。"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
from uuid import uuid4

from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cd2_metadata_client import CloudEntry, DirectoryObservation
from app.service.cloud_drive_budget_service import createCloudReadSession
from app.service.cloud_inventory_service import (
    createCloudInventoryJob, claimCloudInventoryJob, persistCloudInventoryPid,
    executeCloudInventoryQuantum, cloudInventorySummary, cloudInventoryEntries,
)

ROOT = "/synthetic-cloud/movies"


def peakRssBytes():
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
            *[(name, ctypes.c_size_t) for name in ("peakWorking", "working", "peakPaged", "paged",
                "peakNonPaged", "nonPaged", "pagefile", "peakPagefile")]]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError("无法读取本机内存计数")
    return counters.peakWorking


class SyntheticStream:
    def __init__(self, count):
        self.count = count

    def consumeDirectory(self, path, sink, **_options):
        for start in range(0, self.count, 128):
            entries = tuple(CloudEntry(str(index), f"example-{index:08d}.mkv", path + f"/example-{index:08d}.mkv",
                                       False, 1024) for index in range(start, min(start + 128, self.count)))
            sink.messageReceived(len(entries), len(entries) * 100)
            sink.writeEntries(entries)
        return DirectoryObservation(path, (), True, "synthetic_finished", self.count, self.count * 100)


def run(count):
    with tempfile.TemporaryDirectory(prefix="mediaai-inventory-scale-") as temporary:
        root = Path(temporary)
        settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False), cloudDrive=CloudDriveSettings(ROOT))
        ensure_database(settings)
        actor = create_user(settings, "synthetic", "scale", "SyntheticTestOnly123!", ["scan:run"], True)
        context = AuditWriteContext(actor)
        session = createCloudReadSession(settings, requestId=str(uuid4()), endpoint="https://synthetic.invalid",
            rootPath=ROOT, maxRpc=10, durationSeconds=900, auditContext=context)
        jobId = createCloudInventoryJob(settings, requestId=str(uuid4()), sessionId=session["id"], selectedPaths=[ROOT],
            excludedPaths=[], maxEntries=1000000, maxBytes=256 * 1024 * 1024, auditContext=context)
        token = claimCloudInventoryJob(settings, jobId)
        persistCloudInventoryPid(settings, jobId, token, os.getpid())
        beforeRss = peakRssBytes()
        started = time.perf_counter()
        executeCloudInventoryQuantum(settings, jobId, token, SyntheticStream(count))
        saveSeconds = time.perf_counter() - started
        durations = []
        responseBytes = 0
        for index in range(100):
            started = time.perf_counter()
            page = cloudInventoryEntries(settings, jobId, actor.id, afterId=index * max(1, (count - 100) // 99), limit=100)
            durations.append((time.perf_counter() - started) * 1000)
            responseBytes = max(responseBytes, len(json.dumps(page, ensure_ascii=False).encode()))
        physical = {suffix or "main": Path(str(settings.database_path) + suffix).stat().st_size
            if Path(str(settings.database_path) + suffix).exists() else 0 for suffix in ("", "-wal", "-shm")}
        summary = cloudInventorySummary(settings, jobId, actor.id)
        result = {"syntheticEntries": count, "storedEntries": summary["storedEntries"], "saveSeconds": round(saveSeconds, 3),
            "pageQueries": 100, "pageSize": 100, "p95PageMs": round(statistics.quantiles(durations, n=20)[18], 3),
            "maxResponseBytes": responseBytes, "peakRssBytes": peakRssBytes(),
            "peakRssGrowthBytes": max(0, peakRssBytes() - beforeRss), "databaseBytes": physical,
            "realCloudRpc": 0, "contentReadBytes": 0, "temporaryDatabase": True,
            "scopeComplete": summary["scopeComplete"]}
        if summary["storedEntries"] != count or not summary["scopeComplete"]:
            raise RuntimeError("合成基准未完整保存")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="临时库的合成元数据基准，不连接任何云盘")
    parser.add_argument("--entries", type=int, choices=(10000, 100000, 500000, 1000000), default=100000)
    run(parser.parse_args().entries)
