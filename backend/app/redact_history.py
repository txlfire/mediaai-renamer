"""本机历史凭据清理入口；默认只预览计数，不输出日志内容。"""

import argparse
import json
from pathlib import Path
import sqlite3

from app.core.config import load_settings
from app.service.history_redaction_service import redactHistory


def main() -> int:
    parser = argparse.ArgumentParser(description="预览或停服清理历史日志中的 HTTP(S) URL 内嵌凭据")
    parser.add_argument("--config", default="config/config.toml")
    parser.add_argument("--apply", action="store_true", help="执行脱敏，默认只预览")
    parser.add_argument("--confirm-service-stopped", action="store_true", help="确认目标实例已停止，工具不能代替停服")
    parser.add_argument("--max-file-mb", type=int, default=128)
    args = parser.parse_args()
    path = Path(args.config)
    if not path.is_file():
        parser.error("配置文件不存在，不能使用默认实例维护历史记录")
    try:
        report = redactHistory(load_settings(path), apply=args.apply, serviceStopped=args.confirm_service_stopped, maxFileMb=args.max_file_mb)
    except (OSError, sqlite3.Error, ValueError):
        print("维护未完成，请检查实例配置、停服确认、数据库锁和访问权限；不输出原始日志或凭据")
        return 1
    print(json.dumps(report.toDict(), ensure_ascii=False))
    print("数据库与文件不是全局事务；跳过或失败项、旧备份及历史业务路径仍需单独核对，不代表秘密已彻底擦除")
    return 1 if report.failedFiles or report.skippedFiles or report.auditStatus in {"completion_failed", "unavailable"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
