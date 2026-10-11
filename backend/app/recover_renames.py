"""默认只读的重命名遗留槽核对入口，不初始化实例或执行文件动作。"""

import argparse
import json
from pathlib import Path
import sqlite3

from app.core.config import load_settings
from app.service.rename_queue_service import inspectInterruptedRenames, releaseInterruptedRename


def main():
    parser = argparse.ArgumentParser(description="核对重命名遗留槽，不终止进程或重放文件动作")
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-service-stopped", action="store_true")
    parser.add_argument("--operation-id", type=int)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0)
    args = parser.parse_args()
    if not Path(args.config).is_file():
        parser.error("配置文件不存在，不允许核对默认实例")
    if args.apply and (not args.confirm_service_stopped or not args.operation_id or args.operation_id <= 0):
        parser.error("释放槽位需要停服确认及有效批次ID")
    try:
        settings = load_settings(args.config)
        if args.apply:
            releaseInterruptedRename(settings, args.operation_id, confirmStopped=True)
        print(json.dumps(inspectInterruptedRenames(settings, limit=args.limit, afterId=args.after_id), ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"重命名槽核对失败（{type(exc).__name__}），保留原状态；请检查配置、版本、权限及进程状态")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
