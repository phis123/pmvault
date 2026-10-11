"""加密内核：KDF、HKDF、AES-256-GCM、恢复密钥。

设计要点（与方案 v3 一致）：
- KDF 默认 Scrypt（纯 stdlib 可用），若装了 argon2-cffi 则优先 Argon2id；
- 主密码只用于派生 KEK，磁盘上不存主密码与任何可直接解密的材料；
- 校验口令用 auth_hash = HMAC-SHA256(master_key, b"auth")，与加密密钥分离；
- 数据加密统一 AES-256-GCM（认证加密），每次保存换新 nonce。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---------------------------------------------------------------- 常量

SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
TAG_LEN = 16

DEFAULT_KDF = "scrypt"
SCRYPT_N = 1 << 15  # 32768
SCRYPT_R = 8
SCRYPT_P = 1
ARGON2_MEM_KIB = 65536
ARGON2_TIME = 3
ARGON2_PAR = 1

_RECOVERY_ALPHABET = "ABCDEFGHIJKLMNPQRSTUVWXYZ23456789"  # 去掉 I O 0 1 等易混字符
RECOVERY_GROUPS = 6
RECOVERY_GROUP_LEN = 4


class CryptoError(Exception):
    """加密相关错误基类。"""


class WrongPasswordError(CryptoError):
    """口令错误。"""


class TamperError(CryptoError):
    """密文被篡改或损坏。"""


# ---------------------------------------------------------------- 工具

def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def b64u_encode(raw: bytes) -> str:
    """URL 安全、无填充的 Base64（同步字符串用）。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def json_dumps(obj: Any) -> bytes:
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def json_loads(raw: bytes) -> Any:
    import json
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------- KDF

def derive_kek(password: str, salt: bytes, kdf: Optional[Dict[str, Any]] = None) -> bytes:
    """从口令派生 32 字节主密钥。kdf 为 None 时使用默认 scrypt 参数。"""
    kdf = kdf or default_kdf_params()
    algo = kdf.get("algo", "scrypt")
    if algo == "scrypt":
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(kdf.get("n", SCRYPT_N)),
            r=int(kdf.get("r", SCRYPT_R)),
            p=int(kdf.get("p", SCRYPT_P)),
            dklen=KEY_LEN,
            maxmem=0x40000000,
        )
    if algo == "argon2id":
        try:
            from argon2.low_level import hash_secret_raw, Type  # type: ignore
        except Exception as exc:  # pragma: no cover - 取决于是否安装
            raise CryptoError("未安装 argon2-cffi，无法使用 argon2id") from exc
        return hash_secret_raw(
            secret=password.encode("utf-8"),
            salt=salt,
            time_cost=int(kdf.get("t", ARGON2_TIME)),
            memory_cost=int(kdf.get("m", ARGON2_MEM_KIB)),
            parallelism=int(kdf.get("p", ARGON2_PAR)),
            hash_len=KEY_LEN,
            type=Type.ID,
        )
    raise CryptoError(f"未知 KDF 算法：{algo}")


def default_kdf_params() -> Dict[str, Any]:
    """有 argon2-cffi 就用 Argon2id，否则退回 Scrypt。"""
    try:
        import argon2  # noqa: F401
    except Exception:
        return {"algo": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}
    return {"algo": "argon2id", "m": ARGON2_MEM_KIB, "t": ARGON2_TIME, "p": ARGON2_PAR}


def new_salt() -> bytes:
    return os.urandom(SALT_LEN)


def new_nonce() -> bytes:
    return os.urandom(NONCE_LEN)


# ---------------------------------------------------------------- 派生与校验

def auth_hash(master_key: bytes) -> bytes:
    return hmac.new(master_key, b"auth", hashlib.sha256).digest()


def verify_auth(master_key: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(auth_hash(master_key), expected)


def subkey(master_key: bytes, info: str, length: int = KEY_LEN) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=None, info=info.encode("utf-8")
    ).derive(master_key)


# ---------------------------------------------------------------- AEAD

def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> Dict[str, str]:
    nonce = new_nonce()
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {"nonce": b64e(nonce), "data": b64e(ct)}


def decrypt(key: bytes, blob: Dict[str, str], aad: bytes = b"") -> bytes:
    try:
        nonce = b64d(blob["nonce"])
        ct = b64d(blob["data"])
    except Exception as exc:
        raise TamperError("密文结构损坏") from exc
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except Exception as exc:
        raise TamperError("密文无法通过完整性校验（口令错误或数据被篡改）") from exc


# ---------------------------------------------------------------- 恢复密钥

def new_recovery_key() -> str:
    """生成 6 组 4 字符的恢复密钥（去掉易混字符）。"""
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUPS * RECOVERY_GROUP_LEN))
    return "-".join(raw[i : i + RECOVERY_GROUP_LEN] for i in range(0, len(raw), RECOVERY_GROUP_LEN))


def normalize_recovery_key(text: str) -> str:
    cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
    return "-".join(cleaned[i : i + RECOVERY_GROUP_LEN] for i in range(0, len(cleaned), RECOVERY_GROUP_LEN))


def recovery_salt() -> bytes:
    """恢复密钥用途固定盐：恢复密钥本身是高熵随机串，不需要慢 KDF。"""
    return hashlib.sha256(b"pm-vault-recovery-v1").digest()[:SALT_LEN]


# ---------------------------------------------------------------- 口令强度

def password_strength(password: str) -> Dict[str, Any]:
    """粗略强度评估：长度 + 字符集熵，分为 0–4 档。"""
    import math

    if not password:
        return {"score": 0, "label": "空", "entropy": 0.0}
    pool = 0
    if any(c.islower() for c in password):
        pool += 26
    if any(c.isupper() for c in password):
        pool += 26
    if any(c.isdigit() for c in password):
        pool += 10
    if any(not c.isalnum() for c in password):
        pool += 33
    entropy = len(password) * math.log2(pool) if pool else 0.0
    if entropy < 28:
        score = 0
    elif entropy < 45:
        score = 1
    elif entropy < 70:
        score = 2
    elif entropy < 100:
        score = 3
    else:
        score = 4
    labels = ["很弱", "弱", "一般", "强", "很强"]
    return {"score": score, "label": labels[score], "entropy": round(entropy, 1)}
