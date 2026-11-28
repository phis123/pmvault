"""双端统一应用状态管理：多密码库注册表、本机密钥、剪贴板自动清理、自动锁定、主题。"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import flet as ft

from pm_core import (
    Credential,
    UnlockThrottle,
    Vault,
    VaultRegistry,
    crypto,
    settings as settings_mod,
    sorting,
)
from .theme import Palette, get_palette

APP_TITLE = "PMVault"


def resolve_data_dir() -> str:
    """确定数据目录：
    - Android：FLET_APP_STORAGE_DATA
    - 冻结 Windows exe：exe 所在目录下的 data/（保持与现有 E:\\账号密码管理\\data\\ 兼容）
    - 开发运行：仓库根下的 data_dev/（避免误碰用户真实 data/）
    """
    env_override = os.environ.get("PMVAULT_DATA_DIR")
    if env_override:
        os.makedirs(env_override, exist_ok=True)
        return os.path.abspath(env_override)
    flet_storage = os.environ.get("FLET_APP_STORAGE_DATA")
    if flet_storage:
        os.makedirs(flet_storage, exist_ok=True)
        return os.path.abspath(flet_storage)
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
        path = os.path.join(base, "data")
    else:
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(app_root, "data_dev")
    os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)


def detect_device_name() -> str:
    if os.environ.get("FLET_APP_STORAGE_DATA"):
        return "android"
    sys_name = platform.system().lower()
    if sys_name.startswith("win"):
        return "windows"
    return sys_name or "device"


class AppState:
    def __init__(self, data_dir: Optional[str] = None, keyfile_dir: Optional[str] = None) -> None:
        self.data_dir = os.path.abspath(data_dir) if data_dir else resolve_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.settings_path = os.path.join(self.data_dir, "settings.json")
        self.settings: Dict[str, Any] = settings_mod.load(self.settings_path)
        self.registry = VaultRegistry(self.data_dir, keyfile_dir=keyfile_dir)
        self.device_name = detect_device_name()

        self.page: Optional[ft.Page] = None
        self.current_vault_id: str = str(self.settings.get("last_vault_id") or "")
        self.vault: Optional[Vault] = None
        self.active_keyfile_bytes: Optional[bytes] = None
        self.active_master_password_hint: Optional[str] = None  # 仅在旧库升级窗口期短暂留存，升级后立即清空

        self.selected_id: Optional[str] = None
        self.category: str = "all"
        self.active_tag: str = ""
        self.search_query: str = ""
        self.throttle = UnlockThrottle()
        self.last_activity: float = time.monotonic()

        self._clip_timer: Optional[threading.Timer] = None
        self._last_copied_secret: Optional[str] = None

    # ------------------------------------------------------------ 主题与设置
    def palette(self) -> Palette:
        return get_palette(self.page, str(self.settings.get("theme_mode", "system")))

    def apply_theme(self) -> None:
        if self.page is None:
            return
        mode = str(self.settings.get("theme_mode", "system"))
        if mode == "dark":
            self.page.theme_mode = ft.ThemeMode.DARK
        elif mode == "light":
            self.page.theme_mode = ft.ThemeMode.LIGHT
        else:
            self.page.theme_mode = ft.ThemeMode.SYSTEM
        pal = self.palette()
        self.page.bgcolor = pal.bg

    def save_settings(self) -> None:
        settings_mod.save(self.settings_path, self.settings)

    def old_days(self) -> int:
        return max(1, int(self.settings.get("old_password_days", 180)))

    def sort_key(self) -> str:
        return sorting.normalize_sort_key(self.settings.get("sort_key"))

    def sort_desc(self) -> bool:
        return bool(self.settings.get("sort_desc", sorting.DEFAULT_SORT_DESC))

    def set_sort(self, key: str, desc: bool) -> None:
        self.settings["sort_key"] = sorting.normalize_sort_key(key)
        self.settings["sort_desc"] = bool(desc)
        self.save_settings()

    def touch_activity(self) -> None:
        self.last_activity = time.monotonic()

    def is_idle_expired(self) -> bool:
        mins = max(0, int(self.settings.get("auto_lock_minutes", 5)))
        if mins <= 0 or self.vault is None:
            return False
        return (time.monotonic() - self.last_activity) >= mins * 60

    # ------------------------------------------------------------ 多库管理
    def ensure_selected_vault_id(self) -> str:
        vaults = self.registry.list_vaults()
        if not vaults:
            self.current_vault_id = ""
            return ""
        ids = [v["id"] for v in vaults]
        if self.current_vault_id not in ids:
            self.current_vault_id = ids[0]
            self.settings["last_vault_id"] = self.current_vault_id
            self.save_settings()
        return self.current_vault_id

    def select_vault_id(self, vault_id: str) -> None:
        self.current_vault_id = vault_id
        self.settings["last_vault_id"] = vault_id
        self.save_settings()

    # ------------------------------------------------------------ 剪贴板安全复制
    def _set_clipboard(self, value: str) -> None:
        if self.page is None:
            return
        try:
            async def _do() -> None:
                await ft.Clipboard().set(value)
            self.page.run_task(_do)
        except Exception:
            try:
                self.page.set_clipboard(value)  # type: ignore[attr-defined]
            except Exception:
                pass

    def copy_text(self, text: str, label: str = "内容", *, is_secret: bool = False) -> None:
        if self.page is None:
            return
        self.touch_activity()
        self._set_clipboard(text)
        sec = max(0, int(self.settings.get("clipboard_clear_seconds", 20)))
        if is_secret and sec > 0:
            self._schedule_clipboard_clear(text, sec)
            self.toast(f"已复制{label}（{sec} 秒后自动清空剪贴板）")
        else:
            self.toast(f"已复制{label}")

    def _schedule_clipboard_clear(self, copied: str, delay_sec: int) -> None:
        if self._clip_timer is not None:
            self._clip_timer.cancel()
        self._last_copied_secret = copied

        def _clear() -> None:
            if self.page is None or self._last_copied_secret != copied:
                return
            self._set_clipboard("")
            self._last_copied_secret = None

        self._clip_timer = threading.Timer(delay_sec, _clear)
        self._clip_timer.daemon = True
        self._clip_timer.start()

    # ------------------------------------------------------------ 提示与锁定
    def toast(self, message: str, *, error: bool = False) -> None:
        if self.page is None:
            return
        pal = self.palette()
        sb = ft.SnackBar(
            content=ft.Text(message, color="#FFFFFF", size=13),
            bgcolor=pal.danger if error else "#0F172A",
            duration=2600,
        )
        self.page.overlay.append(sb)
        sb.open = True
        self.page.update()

    def lock(self) -> None:
        if self._clip_timer is not None:
            self._clip_timer.cancel()
            self._clip_timer = None
        if self.vault is not None:
            self.vault.lock()
        self.vault = None
        self.active_keyfile_bytes = None
        self.active_master_password_hint = None
        self.selected_id = None
        self.category = "all"
        self.active_tag = ""
        self.search_query = ""

    def load_keyfile_for_current(self) -> Optional[bytes]:
        rec = self.registry.get(self.current_vault_id)
        if rec and rec.keyfile_path and os.path.exists(rec.keyfile_path):
            _, raw = crypto.load_keyfile(rec.keyfile_path)
            return raw
        return None
