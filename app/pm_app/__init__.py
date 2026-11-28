"""pm_app —— 双端（Windows 桌面 + Android 手机/平板）统一响应式 Flet 界面。"""

from .app import main  # noqa: F401
from .state import APP_TITLE  # noqa: F401

__all__ = ["APP_TITLE", "main"]
