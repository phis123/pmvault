"""PMVault 统一入口（Windows 桌面 + Android 共用，flet build 要求根目录存在 main.py）。"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from pm_app.app import main  # noqa: E402

if __name__ == "__main__":
    import flet as ft

    ft.run(main)
