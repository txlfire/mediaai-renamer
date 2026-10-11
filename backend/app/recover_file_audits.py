"""本机文件动作审计核对入口，默认只读，不重放文件操作。"""

import argparse
import json
from pathlib import Path
import sqlite3

from app.core.config import load_settings
from app.service.file_action_audit_service import flushFileActionAuditIntents, inspectFileActionAuditIntents


def main() -> int:
    parser = argparse.ArgumentParser(description="核对及补写文件动作审计，不移动或删除文件")
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-service-stopped", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--after-id", type=int, default=0)
    args = parser.parse_args()
    if not Path(args.config).is_file():
        parser.error("配置文件不存在，不允许核对默认实例")
    if args.apply and not args.confirm_service_stopped:
        parser.error("补写审计需要停服确认；工具不负责停服")
    try:
        settings = load_settings(args.config)
        if args.apply:
            report = flushFileActionAuditIntents(settings, limit=args.limit, afterId=args.after_id)
        else:
            report = inspectFileActionAuditIntents(settings, limit=args.limit, afterId=args.after_id)
    except (OSError, sqlite3.Error, ValueError):
        print("审计核对失败，请检查配置、数据库版本和权限；不会初始化数据库或重放文件操作")
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 1 if args.apply and (report["pending"] or report["has_more"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
