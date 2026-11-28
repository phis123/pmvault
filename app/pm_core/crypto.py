"""加密内核：KDF、HKDF、AES-256-GCM、本机密钥文件（Keyfile）、一次性高熵恢复码、退避限速。

设计要点：
- KDF 默认高强度 Scrypt（N=65536 / 64MB 内存困难型，兼容旧库 N=32768），若装了 argon2-cffi 则优先 Argon2id；
- 主密码（可选叠加 256-bit 本机密钥文件）只用于派生 KEK，磁盘上不存主密码与任何可直接解密的材料；
- 校验口令用 auth_hash = HMAC-SHA256(master_key, b"auth")，与加密密钥通过 HKDF 严格分离；
- 数据加密统一 AES-256-GCM（AEAD），每次保存换新 nonce；
- 一次性恢复码：6 组 4 字符高熵随机串（~120 bit），直接包裹 master_key，可绕过主密码与本机密钥紧急兜底。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---------------------------------------------------------------- 常量

SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
TAG_LEN = 16

DEFAULT_KDF = "scrypt"
SCRYPT_N = 1 << 16  # 65536 (~64MB 内存，对抗离线 GPU 穷举)
SCRYPT_R = 8
SCRYPT_P = 1
ARGON2_MEM_KIB = 65536
ARGON2_TIME = 3
ARGON2_PAR = 1

_RECOVERY_ALPHABET = "ABCDEFGHIJKLMNPQRSTUVWXYZ23456789"  # 去掉 I O 0 1 等易混字符
RECOVERY_GROUPS = 6
RECOVERY_GROUP_LEN = 4

KEYFILE_FORMAT = "pmkey"
KEYFILE_VERSION = 1
KEYFILE_SUFFIX = ".pmkey"


class CryptoError(Exception):
    """加密相关错误基类。"""


class WrongPasswordError(CryptoError):
    """口令错误。"""


class TamperError(CryptoError):
    """密文被篡改或损坏。"""


class KeyfileRequiredError(CryptoError):
    """该保险库启用了本机密钥文件，但未提供。"""

    def __init__(self, expected_id: str = "") -> None:
        msg = f"缺少配对的本机密钥文件（编号 {expected_id}）" if expected_id else "缺少配对的本机密钥文件"
        super().__init__(msg)
        self.expected_id = expected_id


class KeyfileMismatchError(CryptoError):
    """提供的本机密钥文件与保险库不匹配。"""

    def __init__(self, expected_id: str, actual_id: str) -> None:
        super().__init__(f"本机密钥文件不匹配（需要 {expected_id}，实际为 {actual_id}）")
        self.expected_id = expected_id
        self.actual_id = actual_id


# ---------------------------------------------------------------- 编码工具

def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def b64u_encode(raw: bytes) -> str:
    """URL 安全、无填充的 Base64（同步与二维码载荷用）。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def json_dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def json_loads(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------- 本机密钥文件（Keyfile）

def keyfile_fingerprint(raw_key: bytes) -> str:
    """返回 8 位十六进制指纹，用于核对密钥文件配对，不泄露密钥内容。"""
    return hashlib.sha256(raw_key).hexdigest()[:8].upper()


def new_keyfile() -> Tuple[str, bytes]:
    """生成一份全新的 256-bit 本机密钥，返回 (keyfile_id, raw_32_bytes)。"""
    raw = os.urandom(KEY_LEN)
    return keyfile_fingerprint(raw), raw


def format_keyfile_json(raw_key: bytes) -> str:
    doc = {
        "format": KEYFILE_FORMAT,
        "version": KEYFILE_VERSION,
        "id": keyfile_fingerprint(raw_key),
        "key": b64u_encode(raw_key),
    }
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save_keyfile(path: str, raw_key: bytes) -> str:
    """原子写入本机密钥文件，返回绝对路径。"""
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = abs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(format_keyfile_json(raw_key))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, abs_path)
    return abs_path


def parse_keyfile_bytes(content: bytes) -> Tuple[str, bytes]:
    """解析 .pmkey 文件；若用户选了任意非 .pmkey 文件，也对其 SHA-256 归一化为 32 字节密钥。"""
    try:
        doc = json.loads(content.decode("utf-8"))
        if isinstance(doc, dict) and doc.get("format") == KEYFILE_FORMAT and "key" in doc:
            raw = b64u_decode(str(doc["key"]))
            if len(raw) >= 16:
                return keyfile_fingerprint(raw), raw
    except Exception:
        pass
    if not content:
        raise CryptoError("密钥文件为空")
    digest = hashlib.sha256(content).digest()
    return keyfile_fingerprint(digest), digest


def load_keyfile(path: str) -> Tuple[str, bytes]:
    if not os.path.exists(path):
        raise KeyfileRequiredError()
    with open(path, "rb") as fh:
        return parse_keyfile_bytes(fh.read())


def encode_keyfile_qr(raw_key: bytes) -> str:
    """极简二维码文本（~56 字节，极易扫描）。"""
    return f"PMKEY1:{keyfile_fingerprint(raw_key)}:{b64u_encode(raw_key)}"


def decode_keyfile_qr(text: str) -> Tuple[str, bytes]:
    s = (text or "").strip()
    if not s.startswith("PMKEY1:"):
        raise CryptoError("不是有效的本机密钥二维码")
    parts = s.split(":", 2)
    if len(parts) != 3:
        raise CryptoError("本机密钥二维码格式损坏")
    kid, b64_key = parts[1].strip().upper(), parts[2].strip()
    try:
        raw = b64u_decode(b64_key)
    except Exception as exc:
        raise CryptoError("本机密钥二维码内容损坏") from exc
    actual = keyfile_fingerprint(raw)
    if kid and actual != kid:
        raise CryptoError("本机密钥二维码校验失败")
    return actual, raw


def combine_secret(password: str, keyfile_bytes: Optional[bytes] = None) -> bytes:
    """将主密码与可选本机密钥安全组合（域分离），作为 KDF 输入。"""
    pw_bytes = password.encode("utf-8")
    if not keyfile_bytes:
        return pw_bytes
    return pw_bytes + b"\x00pmvault-keyfile-v2\x00" + hashlib.sha256(keyfile_bytes).digest()


# ---------------------------------------------------------------- KDF

def derive_kek(
    password: str,
    salt: bytes,
    kdf: Optional[Dict[str, Any]] = None,
    keyfile_bytes: Optional[bytes] = None,
) -> bytes:
    """从主密码（+ 可选本机密钥）派生 32 字节主密钥。"""
    kdf = kdf or default_kdf_params()
    algo = kdf.get("algo", "scrypt")
    secret_bytes = combine_secret(password, keyfile_bytes)
    if algo == "scrypt":
        return hashlib.scrypt(
            secret_bytes,
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
        except Exception as exc:  # pragma: no cover
            raise CryptoError("未安装 argon2-cffi，无法使用 argon2id") from exc
        return hash_secret_raw(
            secret=secret_bytes,
            salt=salt,
            time_cost=int(kdf.get("t", ARGON2_TIME)),
            memory_cost=int(kdf.get("m", ARGON2_MEM_KIB)),
            parallelism=int(kdf.get("p", ARGON2_PAR)),
            hash_len=KEY_LEN,
            type=Type.ID,
        )
    raise CryptoError(f"未知 KDF 算法：{algo}")


def default_kdf_params() -> Dict[str, Any]:
    """有 argon2-cffi 就用 Argon2id，否则用 64MB 内存困难型 Scrypt。"""
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


# ---------------------------------------------------------------- 一次性高熵恢复码

def new_recovery_key() -> str:
    """生成 6 组 4 字符的恢复密钥（去掉易混字符，~120 bit 熵）。"""
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUPS * RECOVERY_GROUP_LEN))
    return "-".join(raw[i : i + RECOVERY_GROUP_LEN] for i in range(0, len(raw), RECOVERY_GROUP_LEN))


def normalize_recovery_key(text: str) -> str:
    cleaned = "".join(ch for ch in text.upper() if ch.isalnum())
    return "-".join(cleaned[i : i + RECOVERY_GROUP_LEN] for i in range(0, len(cleaned), RECOVERY_GROUP_LEN))


def recovery_salt() -> bytes:
    """保留兼容旧接口。"""
    return hashlib.sha256(b"pm-vault-recovery-v1").digest()[:SALT_LEN]


# ---------------------------------------------------------------- 解锁退避限速（三端共用）

class UnlockThrottle:
    """连续输错主密码后的指数退避控制器（防本机暴力试探）。"""

    def __init__(self, lockout_after: int = 5, max_cooldown: float = 30.0) -> None:
        self.lockout_after = lockout_after
        self.max_cooldown = max_cooldown
        self.failures = 0
        self.locked_until = 0.0

    def register_failure(self) -> float:
        self.failures += 1
        if self.failures >= self.lockout_after:
            wait = min(float(2 ** (self.failures - self.lockout_after + 1)), self.max_cooldown)
            self.locked_until = time.monotonic() + wait
            return wait
        return 0.0

    def register_success(self) -> None:
        self.failures = 0
        self.locked_until = 0.0

    def cooldown_remaining(self) -> float:
        return max(0.0, self.locked_until - time.monotonic())


# ---------------------------------------------------------------- 口令强度

def password_strength(password: str) -> Dict[str, Any]:
    """强度评估：长度 + 字符集熵，分为 0–4 档。"""
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


def validate_master_password(password: str, has_keyfile: bool = False) -> Optional[str]:
    """返回错误提示文本，合法则返回 None。

    启用本机密钥文件时允许更短的好记主密码（>= 6 位）；
    未启用本机密钥文件时要求 >= 8 位且不能为纯单字符。
    """
    if not password:
        return "主密码不能为空"
    min_len = 6 if has_keyfile else 8
    if len(password) < min_len:
        return f"主密码至少需要 {min_len} 位" + ("（已启用本机密钥）" if has_keyfile else "（建议同时启用本机密钥增强离线防护）")
    if len(set(password)) <= 1:
        return "主密码不能全部为相同字符"
    return None
