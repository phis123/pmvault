"""密码生成器：可配置长度与字符集，支持排除易混字符。"""

from __future__ import annotations

import secrets
import string
from typing import Dict, List, Optional

LOWER = string.ascii_lowercase
UPPER = string.ascii_uppercase
DIGITS = string.digits
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/"

CONFUSING = set("Il1O0o|`'\"{}[]()/\\")

DEFAULT_OPTIONS = {
    "length": 20,
    "lower": True,
    "upper": True,
    "digits": True,
    "symbols": True,
    "exclude_confusing": True,
}


def build_alphabet(options: Optional[Dict] = None) -> str:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    pools: List[str] = []
    if opts["lower"]:
        pools.append(LOWER)
    if opts["upper"]:
        pools.append(UPPER)
    if opts["digits"]:
        pools.append(DIGITS)
    if opts["symbols"]:
        pools.append(SYMBOLS)
    if not pools:
        raise ValueError("至少需要选择一种字符类型")
    alphabet = "".join(pools)
    if opts["exclude_confusing"]:
        alphabet = "".join(ch for ch in alphabet if ch not in CONFUSING)
        if not alphabet:
            raise ValueError("排除易混字符后字符集为空")
    return alphabet


def generate_password(options: Optional[Dict] = None) -> str:
    """生成密码，并保证每类所选字符至少出现一次。"""
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    length = int(opts["length"])
    if length < 4:
        raise ValueError("长度至少 4")
    if length > 256:
        raise ValueError("长度上限 256")

    pools: List[str] = []
    for key, chars in (("lower", LOWER), ("upper", UPPER), ("digits", DIGITS), ("symbols", SYMBOLS)):
        if opts[key]:
            pool = chars
            if opts["exclude_confusing"]:
                pool = "".join(ch for ch in chars if ch not in CONFUSING)
            if pool:
                pools.append(pool)
    if not pools:
        raise ValueError("至少需要选择一种字符类型")
    if length < len(pools):
        raise ValueError(f"长度至少 {len(pools)} 才能覆盖所选字符类型")

    alphabet = "".join(pools)
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def generate_passphrase(words: Optional[List[str]] = None, count: int = 4, separator: str = "-") -> str:
    """预留：短语式密码（v2 功能，需词表）。"""
    words = words or []
    if len(words) < 16:
        raise ValueError("词表不足，暂不支持短语模式")
    chosen = [secrets.choice(words) for _ in range(count)]
    return separator.join(chosen)
