"""PyInstaller 入口:以完整包形式启动 stdio 引擎(§11.1 路线 B)。

相对导入要求以包方式加载,不能直接把 `xrd_engine/__main__.py` 当脚本喂给
PyInstaller —— 那会让 `from .api.commands import ...` 在冻结环境里炸。
"""

from __future__ import annotations

import sys

from xrd_engine.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
