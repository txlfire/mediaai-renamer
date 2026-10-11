"""写入新文件内容前保留源文件的 Windows 访问控制。"""

import os
from pathlib import Path
import subprocess


def copyFileAccessControl(source: Path, target: Path) -> None:
    if os.name != "nt":
        return
    env = dict(os.environ, MEDIAAI_REDACT_SOURCE=str(source), MEDIAAI_REDACT_TARGET=str(target))
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); $acl=Get-Acl -LiteralPath $env:MEDIAAI_REDACT_SOURCE; Set-Acl -LiteralPath $env:MEDIAAI_REDACT_TARGET -AclObject $acl"],
            env=env, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("Windows 访问控制复制超时，停止写入文件内容") from exc
    if result.returncode:
        raise OSError("无法保留 Windows 文件访问控制，停止写入文件内容")
