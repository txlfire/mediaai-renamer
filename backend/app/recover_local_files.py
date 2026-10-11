"""本机停服核对入口，默认只预览，不移动或删除真实文件。"""

import argparse
import json
from pathlib import Path
import sqlite3

from app.core.config import load_settings
from app.service.local_file_recovery_service import closeLocalFileActionManually, recoverLocalFileActions


def main() -> int:
    parser = argparse.ArgumentParser(description="核对本地文件动作，仅修复数据库")
    parser.add_argument("--config", default="config/config.toml")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-service-stopped", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--action-id", type=int)
    parser.add_argument("--resolution", choices=("applied", "not_applied"))
    parser.add_argument("--reason")
    parser.add_argument("--evidence")
    args = parser.parse_args()
    configPath = Path(args.config)
    if not configPath.is_file():
        parser.error("配置文件不存在，不允许维护默认实例")
    try:
        settings = load_settings(configPath)
        manualValues = (args.action_id, args.resolution, args.reason, args.evidence)
        if any(value is not None for value in manualValues):
            if not all(value is not None for value in manualValues):
                parser.error("人工结案必须同时提供 --action-id、--resolution、--reason 和 --evidence")
            report = closeLocalFileActionManually(
                settings,
                args.action_id,
                resolution=args.resolution,
                reason=args.reason,
                evidence=args.evidence,
                serviceStopped=args.confirm_service_stopped,
            )
        else:
            report = recoverLocalFileActions(
                settings,
                apply=args.apply,
                serviceStopped=args.confirm_service_stopped,
                limit=args.limit,
            )
    except (OSError, sqlite3.Error, ValueError):
        print("核对失败，请检查配置、数据库版本、权限和停服确认；工具不负责停服")
        return 1
    print(json.dumps(report, ensure_ascii=False))
    if report.get("closed"):
        return 0
    return 1 if report["manual"] or report["failed"] or report["hasMore"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
