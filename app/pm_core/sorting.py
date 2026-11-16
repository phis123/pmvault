"""排序规则：名称 / 创建时间 / 修改时间，支持递增与递减。

名称排序不依赖 locale（APK 里没有 ICU，`locale.strxfrm` 在 Android 上也不可靠），
规则尽量对齐 Windows 资源管理器的「按名称」：
  1. 忽略大小写（casefold）
  2. 连续数字按数值比较：`条目2` 排在 `条目10` 前面
  3. 数字段整体排在文字段之前（`1a` 在 `a1` 前），与资源管理器一致
  4. 中文按 Unicode 码位比较（不转拼音）
排序后仍相等的条目保持原有相对顺序（Python 稳定排序）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Sequence, Tuple

SORT_NAME = "name"
SORT_CREATED = "created"
SORT_UPDATED = "updated"

DEFAULT_SORT_KEY = SORT_CREATED
DEFAULT_SORT_DESC = False

SORT_KEYS: Tuple[str, ...] = (SORT_NAME, SORT_CREATED, SORT_UPDATED)
SORT_LABELS: Dict[str, str] = {
    SORT_NAME: "名称",
    SORT_CREATED: "创建时间",
    SORT_UPDATED: "修改时间",
}

_DIGIT_RUN = re.compile(r"(\d+)")


def normalize_sort_key(key: Any) -> str:
    """把外部传入的值收敛成合法排序键；非法值回退到默认键。"""
    text = str(key or "").strip().lower()
    return text if text in SORT_KEYS else DEFAULT_SORT_KEY


def sort_label(key: Any, desc: bool = False) -> str:
    """紧凑标签，用于状态栏和按钮提示。"""
    return f"{SORT_LABELS[normalize_sort_key(key)]}{'↓' if desc else '↑'}"


def sort_caption(key: Any, desc: bool = False) -> str:
    """完整说明，用于对话框等需要写清楚的地方。"""
    return f"{SORT_LABELS[normalize_sort_key(key)]}（{'递减' if desc else '递增'}）"


def name_key(name: Any) -> Tuple[Tuple[int, Any], ...]:
    """名称排序键：文字段按码位、数字段按数值，忽略大小写。

    每段都带类型标记再比较：既能避免整数与字符串直接比较抛 TypeError，
    又能保证「数字段在前、文字段在后」的次序。
    """
    text = str(name or "").casefold()
    parts: List[Tuple[int, Any]] = []
    # re.split 带捕获组时，偶数下标是文字、奇数下标是数字段
    for index, piece in enumerate(_DIGIT_RUN.split(text)):
        if not piece:
            continue
        if index % 2:
            parts.append((0, int(piece)))
        else:
            parts.append((1, piece))
    return tuple(parts)


def time_key(value: Any) -> float:
    """把存储里的 ISO 时间串转成时间戳；缺失或解析失败按最小值处理。"""
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def _key_name(cred: Any) -> Tuple[Tuple[int, Any], ...]:
    return name_key(getattr(cred, "name", ""))


def _key_created(cred: Any) -> float:
    return time_key(getattr(cred, "created_at", ""))


def _key_updated(cred: Any) -> float:
    return time_key(getattr(cred, "updated_at", ""))


_KEY_FUNCS: Dict[str, Callable[[Any], Any]] = {
    SORT_NAME: _key_name,
    SORT_CREATED: _key_created,
    SORT_UPDATED: _key_updated,
}


def sort_credentials(credentials: Sequence[Any], key: Any = DEFAULT_SORT_KEY,
                     desc: bool = DEFAULT_SORT_DESC) -> List[Any]:
    """按指定键排序并返回新列表；相等条目保持原有相对顺序。"""
    keyfunc = _KEY_FUNCS[normalize_sort_key(key)]
    return sorted(credentials, key=keyfunc, reverse=bool(desc))
