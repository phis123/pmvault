"""应用设置：明文 JSON（只放非敏感项），与保险库同目录。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

DEFAULT_SETTINGS: Dict[str, Any] = {
    "vault_path": "",            # 空表示用默认 data/vault.pmvault
    "auto_lock_minutes": 5,
    "clipboard_clear_seconds": 20,
    "reveal_seconds": 10,
    "generator": {
        "length": 20,
        "lower": True,
        "upper": True,
        "digits": True,
        "symbols": True,
        "exclude_confusing": True,
    },
    "old_password_days": 180,
    # 列表排序（与安卓端同一套键；默认创建时间递增 = 旧版插入顺序）
    "sort_key": "created",
    "sort_desc": False,
}


def load(path: str) -> Dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    if os.path.exists(path):
        try:
            raw = json.loads(open(path, "r", encoding="utf-8").read())
            if isinstance(raw, dict):
                data.update(raw)
                gen = dict(DEFAULT_SETTINGS["generator"])
                gen.update(raw.get("generator") or {})
                data["generator"] = gen
        except Exception:
            pass
    return data


def save(path: str, settings: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True))
    os.replace(tmp, path)
