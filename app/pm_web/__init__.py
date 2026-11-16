"""pm_web —— 本地回环 HTTP 服务 + 浏览器界面（不依赖 Tcl/Tk）。"""

from .server import Session, run  # noqa: F401

__all__ = ["Session", "run"]
