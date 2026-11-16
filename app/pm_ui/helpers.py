"""剪贴板与空闲自动锁定的辅助实现（纯标准库）。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional


class ClipboardGuard:
    """复制后延迟清空剪贴板；清空前确认内容仍是自己写进去的那一份。"""

    def __init__(self, root: tk.Misc, clear_after_seconds: int = 20) -> None:
        self.root = root
        self.clear_after_seconds = clear_after_seconds
        self._token = 0
        self._expected: Optional[str] = None

    def copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self._expected = text
        self._token += 1
        token = self._token
        self.root.after(self.clear_after_seconds * 1000, lambda: self._clear(token))

    def _clear(self, token: int) -> None:
        if token != self._token:
            return  # 期间又复制了别的内容，取消本次清理
        try:
            current = self.root.clipboard_get()
        except Exception:
            current = None
        if current == self._expected:
            try:
                self.root.clipboard_clear()
                self.root.update_idletasks()
            except Exception:
                pass
        self._expected = None


class AutoLock:
    """空闲计时器：达到阈值后回调锁定；活动事件会重置计时。"""

    def __init__(self, root: tk.Misc, minutes: int, on_lock: Callable[[], None]) -> None:
        self.root = root
        self.minutes = max(1, int(minutes))
        self.on_lock = on_lock
        self._timer: Optional[str] = None
        self._enabled = False

    # ------------------------------------------------------------ 控制
    def start(self) -> None:
        self._enabled = True
        self._bind()
        self.touch()

    def stop(self) -> None:
        self._enabled = False
        if self._timer:
            try:
                self.root.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None

    def set_minutes(self, minutes: int) -> None:
        self.minutes = max(1, int(minutes))
        self.touch()

    def touch(self, _event=None) -> None:
        if not self._enabled:
            return
        if self._timer:
            try:
                self.root.after_cancel(self._timer)
            except Exception:
                pass
        self._timer = self.root.after(self.minutes * 60 * 1000, self._fire)

    def _fire(self) -> None:
        self._timer = None
        if self._enabled:
            self.on_lock()

    def _bind(self) -> None:
        for seq in ("<Any-KeyPress>", "<Any-Button>", "<Motion>", "<MouseWheel>"):
            try:
                self.root.bind_all(seq, self.touch, add="+")
            except Exception:
                pass

    # ------------------------------------------------------------ 状态
    def remaining_text(self) -> str:
        if not self._enabled:
            return "未启用"
        return f"{self.minutes} 分钟内无操作自动锁定"
