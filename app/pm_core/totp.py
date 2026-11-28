"""TOTP 两步验证（RFC 6238 / RFC 4226），100% 纯 Python 标准库实现。

兼容 Google Authenticator / Microsoft Authenticator / Authy 等标准 2FA：
- 支持直接粘贴 Base32 密钥（自动忽略空格、连字符、大小写）
- 支持直接粘贴 otpauth://totp/... URI（自动解析 secret / digits / period / algorithm / issuer）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse


class TotpError(ValueError):
    """TOTP 密钥格式或参数无效。"""


_HASH_ALGOS = {
    "SHA1": hashlib.sha1,
    "SHA256": hashlib.sha256,
    "SHA512": hashlib.sha512,
}


def _normalize_base32(secret: str) -> bytes:
    cleaned = "".join(ch for ch in (secret or "").upper() if ch.isalnum())
    if not cleaned:
        raise TotpError("2FA 密钥为空")
    pad = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + pad, casefold=True)
    except Exception as exc:
        raise TotpError("2FA 密钥不是有效的 Base32 编码") from exc


def parse_totp_config(raw: str) -> Dict[str, Any]:
    """解析纯 Base32 密钥或 otpauth://totp/... URI。"""
    text = (raw or "").strip()
    if not text:
        raise TotpError("2FA 密钥为空")

    digits = 6
    period = 30
    algorithm = "SHA1"
    issuer = ""
    account = ""
    secret_str = text

    if text.lower().startswith("otpauth://"):
        parsed = urlparse(text)
        if parsed.netloc.lower() != "totp":
            raise TotpError("仅支持 otpauth://totp/ 类型的 2FA")
        qs = parse_qs(parsed.query)
        secret_str = (qs.get("secret") or [""])[0].strip()
        if not secret_str:
            raise TotpError("otpauth 链接中缺少 secret 参数")
        if "digits" in qs:
            try:
                digits = int(qs["digits"][0])
            except ValueError as exc:
                raise TotpError("digits 参数无效") from exc
        if "period" in qs:
            try:
                period = int(qs["period"][0])
            except ValueError as exc:
                raise TotpError("period 参数无效") from exc
        if "algorithm" in qs:
            algorithm = qs["algorithm"][0].strip().upper()
        issuer = (qs.get("issuer") or [""])[0].strip()
        label = unquote(parsed.path.lstrip("/"))
        if ":" in label:
            lb_issuer, lb_acc = label.split(":", 1)
            issuer = issuer or lb_issuer.strip()
            account = lb_acc.strip()
        else:
            account = label.strip()

    if digits not in (6, 7, 8):
        raise TotpError(f"不支持的验证码位数：{digits}")
    if period <= 0 or period > 300:
        raise TotpError(f"不支持的刷新周期：{period} 秒")
    if algorithm not in _HASH_ALGOS:
        raise TotpError(f"不支持的哈希算法：{algorithm}")

    secret_bytes = _normalize_base32(secret_str)
    return {
        "secret_bytes": secret_bytes,
        "digits": digits,
        "period": period,
        "algorithm": algorithm,
        "issuer": issuer,
        "account": account,
    }


def generate_totp(raw_secret: str, for_time: Optional[float] = None) -> Tuple[str, int, int]:
    """返回 (验证码字符串, 剩余秒数, 总周期秒数)。"""
    cfg = parse_totp_config(raw_secret)
    now = time.time() if for_time is None else float(for_time)
    period = int(cfg["period"])
    digits = int(cfg["digits"])
    counter = int(now // period)
    remaining = period - int(now % period)

    msg = struct.pack(">Q", counter)
    digest = hmac.new(cfg["secret_bytes"], msg, _HASH_ALGOS[cfg["algorithm"]]).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = str(code_int % (10 ** digits)).zfill(digits)
    return code, remaining, period


def format_code(code: str) -> str:
    """将 6 位验证码格式化为 `123 456` 便于肉眼核对。"""
    if len(code) == 6:
        return f"{code[:3]} {code[3:]}"
    if len(code) == 8:
        return f"{code[:4]} {code[4:]}"
    return code
