"""统一应用设置（桌面与移动端共用）：明文 JSON，仅存非敏感偏好。"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict

from .sorting import DEFAULT_SORT_DESC, DEFAULT_SORT_KEY

DEFAULT_SETTINGS: Dict[str, Any] = {
    "last_vault_id": "",
    "theme_mode": "system",          # "system" | "light" | "dark"
    "auto_lock_minutes": 5,
    "clipboard_clear_seconds": 20,
    "reveal_seconds": 10,
    "old_password_days": 180,
    "sort_key": DEFAULT_SORT_KEY,
    "sort_desc": DEFAULT_SORT_DESC,
    "generator": {
        "length": 20,
        "lower": True,
        "upper": True,
        "digits": True,
        "symbols": True,
        "exclude_confusing": True,
    },
}


def load(path: str) -> Dict[str, Any]:
    data = copy.deepcopy(DEFAULT_SETTINGS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.loads(fh.read())
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k != "generator":
                        data[k] = v
                gen = copy.deepcopy(DEFAULT_SETTINGS["generator"])
                if isinstance(raw.get("generator"), dict):
                    gen.update(raw["generator"])
                data["generator"] = gen
        except Exception:
            pass
    return data


def save(path: str, settings: Dict[str, Any]) -> None:
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = abs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, abs_path)
