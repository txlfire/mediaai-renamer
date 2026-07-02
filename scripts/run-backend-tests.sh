#!/usr/bin/env bash
# 用途：在 Linux/macOS 环境运行后端 unittest 测试套件。
# 关键步骤：进入项目根目录 -> 设置 PYTHONPATH -> 选择可用 Python -> 运行测试。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# 后端包位于 backend 目录，测试前需要把它加入 Python 模块搜索路径。
export PYTHONPATH="backend"

# 优先使用项目虚拟环境 Python，找不到时降级到系统 python3/python。
if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

"$PYTHON" -m unittest discover backend/tests -v
