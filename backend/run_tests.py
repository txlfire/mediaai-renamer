"""在独立临时配置中运行回归，避免模块级应用初始化修改默认实例。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    temporary = tempfile.TemporaryDirectory(prefix="mediaai-suite-")
    isolated = Path(temporary.name)
    childReturnCode: int | None = None
    cleanupFailed = False
    try:
        config = isolated / "config.toml"
        config.write_text(
            f'[paths]\ndata_dir="{isolated.as_posix()}/data"\n'
            f'[logging]\nlog_dir="{isolated.as_posix()}/logs"\nconsole_output=false\n',
            encoding="utf-8",
        )
        environment = {**os.environ, "MEDIAAI_CONFIG_PATH": str(config),
                       "PYTHONPATH": os.pathsep.join((str(root / "backend"), str(root / "backend/tests"))),
                       "PYTHONIOENCODING": "utf-8"}
        arguments = sys.argv[1:] or ["discover", "backend/tests", "-v"]
        childReturnCode = subprocess.run(
            [sys.executable, "-m", "unittest", *arguments],
            cwd=root,
            env=environment,
            check=False,
        ).returncode
    finally:
        try:
            temporary.cleanup()
        except OSError as exc:
            cleanupFailed = True
            print(
                f"测试临时目录清理失败，请人工检查残留路径：{isolated}（{type(exc).__name__}）",
                file=sys.stderr,
            )

    if childReturnCode is None:
        raise RuntimeError("测试子进程未返回退出码")
    if childReturnCode != 0:
        return childReturnCode
    return 1 if cleanupFailed else 0


if __name__ == "__main__":
    raise SystemExit(main())
