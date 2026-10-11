"""Flet 应用入口（必须是应用目录根下的 main.py，这是 flet build 的硬要求）。

Flet 的规则：`--module-name` 只取文件名，然后到应用目录根下找 `main.py`。
所以真正的入口放在这里，界面逻辑在 `pm_android/`、加密内核在 `pm_core/`。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from pm_android.app import main  # noqa: E402

if __name__ == "__main__":
    import flet as ft

    ft.run(main)
