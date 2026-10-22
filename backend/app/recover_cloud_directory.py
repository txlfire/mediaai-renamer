"""仅本机显式配置的目录探测核对；不初始化数据库，不连接云端。"""

import argparse
import json
from pathlib import Path

from app.core.config import load_settings
from app.service.cloud_directory_probe_service import inspectDirectoryProbes, releaseDirectoryProbeAfterReview


def main():
    parser = argparse.ArgumentParser(description="目录整理本机核对，不重放远端请求")
    parser.add_argument("--config", required=True)
    parser.add_argument("--release-probe")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-process-stopped", action="store_true")
    parser.add_argument("--confirm-remote-idle", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.release_probe:
        parser.error("--apply须同时指定--release-probe")
    if not Path(args.config).is_file():
        parser.error("须指定已存在的配置文件，不读取默认实例")
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError):
        parser.error("指定配置无法读取或不合法")
    if args.release_probe and args.apply:
        releaseDirectoryProbeAfterReview(settings, args.release_probe, confirmProcessStopped=args.confirm_process_stopped,
                                         confirmRemoteIdle=args.confirm_remote_idle)
    print(json.dumps({"probes": inspectDirectoryProbes(settings), "networkAccess": False, "replayed": False,
                      "applied": bool(args.apply and args.release_probe)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
