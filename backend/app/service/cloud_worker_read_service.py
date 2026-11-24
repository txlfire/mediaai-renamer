"""仅供监督进程重读本地状态；不用于RPC、批准、写事务或动作重放。"""

import sqlite3
import time

from app.service.cloud_inventory_service import CloudInventoryError


def readCloudSupervision(read, *, deadline, stop=None):
    for attempt in range(3):
        try:
            return read()
        except CloudInventoryError as exc:
            if (exc.sqliteErrorCode != sqlite3.SQLITE_READONLY_ROLLBACK or attempt == 2
                    or stop is not None and stop.is_set() or time.monotonic() >= deadline):
                raise
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))
