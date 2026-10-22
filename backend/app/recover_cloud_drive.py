"""CD2本机租约核对；默认只读，不导入应用或自动初始化数据库。"""

import argparse
import json
from pathlib import Path

from app.core.config import load_settings
from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings
from app.service.cloud_drive_budget_service import (
    CloudBudgetError, cloudBudgetStatus, releaseCloudLeaseAfterReview,
)
from app.service.cloud_inventory_service import CloudInventoryError
from app.service.cloud_inventory_worker_service import inspectCloudInventoryWorker, releaseCloudInventoryWorkerAfterReview


def main() -> int:
    parser = argparse.ArgumentParser(description="核对CD2未决槽，不连接云盘或重放请求")
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lease-id")
    parser.add_argument("--inventory-job-id")
    parser.add_argument("--confirm-clients-stopped", action="store_true")
    parser.add_argument("--confirm-remote-idle", action="store_true")
    args = parser.parse_args()
    if not Path(args.config).is_file():
        parser.error("配置文件不存在，不允许回退默认实例")
    if args.lease_id and args.inventory_job_id:
        parser.error("一次只核对一种槽，任务槽和RPC租约不能合并释放")
    if args.apply and (not (args.lease_id or args.inventory_job_id) or not args.confirm_clients_stopped or not args.confirm_remote_idle):
        parser.error("释放须提供租约或盘点任务UUID，并确认相关客户端停止且远端静止")
    try:
        settings = resolveCloudDriveSettings(load_settings(args.config))
        if args.inventory_job_id:
            if args.apply:
                releaseCloudInventoryWorkerAfterReview(settings, args.inventory_job_id,
                    confirmClientsStopped=args.confirm_clients_stopped, confirmRemoteIdle=args.confirm_remote_idle)
            result = inspectCloudInventoryWorker(settings, args.inventory_job_id)
        elif args.apply:
            releaseCloudLeaseAfterReview(settings, args.lease_id,
                confirmClientsStopped=args.confirm_clients_stopped, confirmRemoteIdle=args.confirm_remote_idle)
            result = cloudBudgetStatus(settings, includeLeaseIdentity=True)
        else:
            result = cloudBudgetStatus(settings, includeLeaseIdentity=True)
    except (CloudBudgetError, CloudInventoryError) as exc:
        print(str(exc))
        return 1
    except (ValueError, OSError):
        print("核对失败，请检查配置、版本和本机进程；未连接云盘或重放请求")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
