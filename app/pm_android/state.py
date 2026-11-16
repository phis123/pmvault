"""Android 版的状态与设置：与桌面版共用同一套数据格式。

数据位置：Android 上 Flet 会把应用私有目录给到 FLET_APP_STORAGE_DATA；
桌面预览时退回到脚本目录下的 data\\。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from pm_core import Vault, crypto
from pm_core.sorting import DEFAULT_SORT_DESC, DEFAULT_SORT_KEY, normalize_sort_key

APP_TITLE = "账号密码管理"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "sort_key": DEFAULT_SORT_KEY,
    "sort_desc": DEFAULT_SORT_DESC,
    "auto_lock_minutes": 5,
    "clipboard_clear_seconds": 20,
    "reveal_seconds": 10,
    "old_password_days": 180,
    "generator": {
        "length": 20,
        "lower": True,
        "upper": True,
        "digits": True,
        "symbols": True,
        "exclude_confusing": True,
    },
}


def data_dir() -> str:
    """Android 用系统给的应用私有目录；桌面预览用脚本旁的 data\\。"""
    env = os.environ.get("FLET_APP_STORAGE_DATA")
    if env:
        path = env
    else:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_android")
    os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


def vault_path() -> str:
    return os.path.join(data_dir(), "vault.pmvault")


def load_settings() -> Dict[str, Any]:
    data = json.loads(json.dumps(DEFAULT_SETTINGS))
    path = settings_path()
    if os.path.exists(path):
        try:
            raw = json.loads(open(path, encoding="utf-8").read())
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k != "generator"})
                gen = dict(DEFAULT_SETTINGS["generator"])
                gen.update(raw.get("generator") or {})
                data["generator"] = gen
        except Exception:
            pass
    return data


def save_settings(settings: Dict[str, Any]) -> None:
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True))
    os.replace(tmp, path)


class AppState:
    """界面共享状态。"""

    def __init__(self) -> None:
        self.settings: Dict[str, Any] = load_settings()
        self.vault: Optional[Vault] = None
        self.page: Any = None
        self.selected_id: Optional[str] = None
        self.revealed: Dict[str, str] = {}      # entry_id -> 明文密码（仅本次会话内存）
        self.sync_passphrase: str = ""
        self.pending_recovery_key: Optional[str] = None
        self.last_activity: float = 0.0

    # ------------------------------------------------------------ 便捷属性
    @property
    def unlocked(self) -> bool:
        return self.vault is not None

    def vault_file(self) -> str:
        return vault_path()

    def vault_exists(self) -> bool:
        return os.path.exists(vault_path())

    def old_days(self) -> int:
        return int(self.settings.get("old_password_days", 180))

    def generator_defaults(self) -> Dict[str, Any]:
        return dict(self.settings.get("generator") or DEFAULT_SETTINGS["generator"])

    # ------------------------------------------------------------ 排序
    def sort_key(self) -> str:
        return normalize_sort_key(self.settings.get("sort_key"))

    def sort_desc(self) -> bool:
        return bool(self.settings.get("sort_desc", DEFAULT_SORT_DESC))

    def set_sort(self, key: str, desc: bool) -> None:
        """记住排序选择：只写 settings.json，不碰加密库。"""
        self.settings["sort_key"] = normalize_sort_key(key)
        self.settings["sort_desc"] = bool(desc)
        save_settings(self.settings)

    # ------------------------------------------------------------ 锁定
    def lock(self) -> None:
        if self.vault is not None:
            self.vault.lock()
        self.vault = None
        self.selected_id = None
        self.revealed.clear()
        self.sync_passphrase = ""

    # ------------------------------------------------------------ 统计
    def markers(self, cred) -> list:
        """列表上要显示的小标记。"""
        flags = []
        strength = crypto.password_strength(cred.password)
        if cred.password and strength["score"] <= 1:
            flags.append(("弱", "#c0392b"))
        if self.vault is not None:
            dup = [c for c in self.vault.credentials if c.password and c.password == cred.password and c.id != cred.id]
            if dup:
                flags.append(("重", "#b4761a"))
        if cred.age_days() > self.old_days():
            flags.append(("旧", "#5c6879"))
        return flags

    def duplicate_names(self, cred) -> list:
        if self.vault is None:
            return []
        return [c.name for c in self.vault.credentials if c.password and c.password == cred.password and c.id != cred.id]

    def summary(self) -> Dict[str, Any]:
        if self.vault is None:
            return {"count": 0, "revision": 0, "kdf": "-", "weak": 0, "old": 0}
        stats = self.vault.stats()
        return {
            "count": stats["count"],
            "revision": stats["revision"],
            "kdf": stats["kdf"],
            "weak": stats["weak"],
            "old": stats["old"],
        }
