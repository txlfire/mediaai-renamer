"""默认只读的回滚遗留槽核对入口，不初始化实例或执行文件动作。"""

import argparse
import json
from pathlib import Path
import sqlite3

from app.core.config import load_settings
from app.service.rollback_queue_service import inspectInterruptedRollbacks, releaseInterruptedRollback


def main():
    parser = argparse.ArgumentParser(description="核对回滚遗留槽，不终止进程或重放文件动作")
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-service-stopped", action="store_true")
    parser.add_argument("--plan-id", type=int)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0)
    args = parser.parse_args()
    if not Path(args.config).is_file():
        parser.error("配置文件不存在，不允许核对默认实例")
    if args.apply and (not args.confirm_service_stopped or not args.plan_id or args.plan_id <= 0):
        parser.error("释放槽位需要停服确认及有效计划ID")
    try:
        settings = load_settings(args.config)
        if args.apply:
            releaseInterruptedRollback(settings, args.plan_id, confirmStopped=True)
        print(json.dumps(inspectInterruptedRollbacks(settings, limit=args.limit, afterId=args.after_id), ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"回滚槽核对失败（{type(exc).__name__}），保留原状态；请检查配置、版本、权限及进程状态")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
