"""本机管理员密码恢复；不导入应用入口，不打印凭据。"""

import argparse
from pathlib import Path
import sqlite3

from app.core.config import AppSettings, load_settings
from app.service.audit_service import record_audit_event
from app.service.auth_service import AuthServiceError, reset_admin_password


def recoverAdminPassword(settings: AppSettings) -> None:
    """先记录恢复意图；密码、会话撤销与完成审计一起提交。"""

    if not settings.database_path.is_file():
        raise ValueError("数据库不存在，请确认正在恢复的是已有实例")
    if not settings.auth.admin_password_reset_enabled:
        raise ValueError("未启用 admin 密码重置功能，请先修改配置文件")
    record_audit_event(
        settings, event_type="auth.password", action="reset_admin_password",
        result="started", summary="本机管理员恢复已授权",
        actor_name="local-recovery", target_type="user",
    )
    reset_admin_password(settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="在服务机器上恢复 admin，须先开启配置中的恢复开关")
    parser.add_argument("--config", default="config/config.toml", help="实例配置文件路径")
    args = parser.parse_args()
    configPath = Path(args.config)
    if not configPath.is_file():
        parser.error("配置文件不存在，不能使用默认配置恢复其他实例")
    try:
        recoverAdminPassword(load_settings(configPath))
    except (AuthServiceError, ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except (OSError, sqlite3.Error):
        print("恢复失败，请检查配置、数据库权限和恢复意图记录")
        return 1
    print("管理员密码已恢复，旧会话已失效；请登录改密，并关闭配置中的恢复开关")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
