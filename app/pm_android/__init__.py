"""pm_android —— Android 版界面（Flet）。

与桌面版的关系：
- **加密内核完全复用** `pm_core`（crypto / models / generator / vault / sync），一行不改；
- 界面是本项目第三套实现：Android 上没有 Tcl/Tk（Tkinter 不能用），Flet 也不吃 HTML（Web 版不能用）。

布局按手机设计：一次只显示一个页面，用状态切换（Flet 1.0 已没有 page.views）。
"""

from .app import main  # noqa: F401
from .state import APP_TITLE, AppState, data_dir, load_settings, save_settings  # noqa: F401
from .screens import LoginScreen, VaultScreen  # noqa: F401

__all__ = ["main", "AppState", "APP_TITLE", "data_dir", "load_settings", "save_settings",
           "LoginScreen", "VaultScreen"]
