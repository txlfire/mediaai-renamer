"""迁移遗留槽默认只读；确认进程实际退出后仅释放预约。"""

import argparse
import json
from pathlib import Path
import sqlite3

from app.core.config import load_settings
from app.service.file_task_queue_service import inspectInterruptedFileTasks, releaseInterruptedFileTask


def main():
    parser = argparse.ArgumentParser(description="核对迁移遗留槽，不重放文件动作")
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-service-stopped", action="store_true")
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0)
    args = parser.parse_args()
    if not Path(args.config).is_file():
        parser.error("配置不存在，不核对默认实例")
    if args.apply and (not args.confirm_service_stopped or not args.task_id or args.task_id <= 0):
        parser.error("释放槽位需要停服确认及有效任务ID")
    try:
        settings = load_settings(args.config)
        if args.apply:
            releaseInterruptedFileTask(settings, "pending_move", args.task_id, confirmStopped=True)
        print(json.dumps(inspectInterruptedFileTasks(settings, "pending_move", limit=args.limit, afterId=args.after_id), ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"迁移槽核对失败（{type(exc).__name__}），保留原状态")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
