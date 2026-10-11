"""加密同步：把整个保险库压成「分片字符串」，可在任意端上传/取回。

格式：PM1-<版本>-<序号>/<总片数>-<Base64URL 密文>
口令与主密码分离：同步口令 → KEK_sync；服务端只能看到密文。
"""

from __future__ import annotations

import hashlib
import zlib
from typing import Any, Dict, List, Optional

from . import crypto
from .models import Credential

MAGIC = "PM1"
CHUNK_CHARS = 800
SYNC_KDF = {"algo": "scrypt", "n": 1 << 15, "r": 8, "p": 1}


class SyncError(Exception):
    pass


class MissingChunksError(SyncError):
    def __init__(self, missing: List[int], total: int) -> None:
        super().__init__(f"缺少第 {', '.join(str(m) for m in missing)}/{total} 片")
        self.missing = missing
        self.total = total


class SyncChecksumError(SyncError):
    pass


def _sync_key(passphrase: str, salt: bytes) -> bytes:
    return crypto.derive_kek(passphrase, salt, SYNC_KDF)


def pack(credentials: List[Credential], passphrase: str, revision: int, device: str,
         chunk_chars: int = CHUNK_CHARS) -> List[str]:
    """把条目列表打成若干片同步字符串。"""
    payload = {
        "v": int(revision),
        "device": device,
        "count": len(credentials),
        "credentials": [c.to_dict() for c in credentials],
    }
    plain = crypto.json_dumps(payload)
    checksum = hashlib.sha256(plain).hexdigest()
    compressed = zlib.compress(plain, 9)
    salt = crypto.new_salt()
    key = _sync_key(passphrase, salt)
    body = {"salt": crypto.b64e(salt), "checksum": checksum, "blob": crypto.encrypt(key, compressed, aad=MAGIC.encode())}
    encoded = crypto.b64u_encode(crypto.json_dumps(body))
    chunks = [encoded[i : i + chunk_chars] for i in range(0, len(encoded), chunk_chars)] or [""]
    total = len(chunks)
    return [f"{MAGIC}-{revision}-{i + 1:02d}/{total:02d}-{chunk}" for i, chunk in enumerate(chunks)]


def unpack(lines: List[str], passphrase: str) -> Dict[str, Any]:
    """从若干片同步字符串还原。缺片/口令错/被改都会明确报错。"""
    parsed: Dict[int, str] = {}
    total: Optional[int] = None
    version: Optional[int] = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            head, chunk = line.split("-", 1)
            if head != MAGIC:
                raise ValueError
            ver, rest = chunk.split("-", 1)
            index_part, rest2 = rest.split("/", 1)
            total_part, chunk_body = rest2.split("-", 1)
            idx = int(index_part)
            tot = int(total_part)
        except Exception as exc:
            raise SyncError("同步字符串格式不正确（应形如 PM1-3-01/03-xxxx）") from exc
        if total is None:
            total, version = tot, int(ver)
        elif total != tot:
            raise SyncError("分片来自不同的同步包（总片数不一致）")
        parsed[idx] = parsed.get(idx, "") + chunk_body

    if total is None:
        raise SyncError("没有可解析的分片")
    missing = [i for i in range(1, total + 1) if i not in parsed]
    if missing:
        raise MissingChunksError(missing, total)

    encoded = "".join(parsed[i] for i in range(1, total + 1))
    try:
        body = crypto.json_loads(crypto.b64u_decode(encoded))
        key = _sync_key(passphrase, crypto.b64d(body["salt"]))
        compressed = crypto.decrypt(key, body["blob"], aad=MAGIC.encode())
        plain = zlib.decompress(compressed)
    except crypto.TamperError as exc:
        raise SyncError("同步口令错误或数据被篡改") from exc
    except Exception as exc:
        raise SyncError(f"同步数据无法解析：{exc}") from exc

    if hashlib.sha256(plain).hexdigest() != body.get("checksum"):
        raise SyncChecksumError("校验和不匹配：数据不完整或被修改")

    data = crypto.json_loads(plain)
    data["credentials"] = [Credential.from_dict(c) for c in data.get("credentials", [])]
    return data


def resolve_conflict(local_revision: int, remote_revision: int, local_device: str, remote_device: str) -> str:
    """返回 'push' | 'pull' | 'conflict'。"""
    if remote_revision == local_revision and remote_device == local_device:
        return "none"
    if remote_revision > local_revision:
        return "pull"
    if remote_revision < local_revision:
        return "push"
    return "conflict" if local_device != remote_device else "none"


def estimate(credential_count: int) -> Dict[str, int]:
    """粗略估算分片数量（用于界面提示）。"""
    approx_bytes = 260 * max(credential_count, 1)
    approx_encoded = int(approx_bytes * 0.6) + 200
    return {"bytes": approx_bytes, "chunks": max(1, (approx_encoded + CHUNK_CHARS - 1) // CHUNK_CHARS)}
