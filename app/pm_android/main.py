"""Android 版入口（Flet 打包时的 main.py）。

关于导入路径：
- Flet 打包时只把**本目录**和依赖目录加入 sys.path，所以发布时 `pm_core` 会被拷贝到本目录内
  （见 09 分区的 work/build_android_tree.py，它负责生成待推送的目录树）。
- 本地开发时 `pm_core` 在上一级目录，下面这段引导会自动加上父目录，两边都能跑。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for candidate in (_HERE, _PARENT):
    if os.path.isdir(os.path.join(candidate, "pm_core")) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from pm_android.app import main  # noqa: E402

if __name__ == "__main__":
    import flet as ft

    ft.run(main)
