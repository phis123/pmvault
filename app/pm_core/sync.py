"""加密同步：
- 新版导出与导入（PM2 双通道互补模式）：
  1. 加密文件（.pmvsync）：用一次性随机 256-bit 传输密钥加密整个密码库（文件内不含任何钥匙，可自由传输）
  2. 互补二维码（PMQR2:...）：仅约 160~260 字节，存一次性传输密钥 + 文件指纹 + 可选携带（登录密码、本机密钥、一次性恢复码）
  → 文件与二维码缺一不可，少任何一个都无法解开！
- 旧版兼容（PM1 口令模式）：
  仅保留只读解包 `unpack` / `unpack_file`，用于无损导入旧版 1.0.x 导出的 .pmvsync，不再以旧格式导出。
"""

from __future__ import annotations

import datetime
import hashlib
import os
import zlib
from typing import Any, Dict, List, Optional

from . import crypto
from .models import Credential

LEGACY_MAGIC = "PM1"
DUAL_MAGIC = "PM2"
QR_PREFIX = "PMQR2:"
CHUNK_CHARS = 800
SYNC_KDF = {"algo": "scrypt", "n": 1 << 15, "r": 8, "p": 1}

FILE_SUFFIX = ".pmvsync"


class SyncError(Exception):
    pass


class MissingChunksError(SyncError):
    def __init__(self, missing: List[int], total: int) -> None:
        super().__init__(f"缺少第 {', '.join(str(m) for m in missing)}/{total} 片")
        self.missing = missing
        self.total = total


class SyncChecksumError(SyncError):
    pass


# ------------------------------------------------------------------ 格式识别

def detect_format(lines: List[str]) -> str:
    """返回 'PM2'（新版双通道）| 'PM1'（旧版口令）| 'UNKNOWN'。"""
    for raw in lines[:200]:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(DUAL_MAGIC + "-"):
            return DUAL_MAGIC
        if s.startswith(LEGACY_MAGIC + "-"):
            return LEGACY_MAGIC
    return "UNKNOWN"


def looks_like_sync_file(text: str) -> bool:
    return detect_format(text.splitlines()) in (DUAL_MAGIC, LEGACY_MAGIC)


# ------------------------------------------------------------------ 新版 PM2：双通道互补导出 / 导入

def pack_dual_channel(
    credentials: List[Credential],
    revision: int,
    device: str,
    *,
    vault_name: str = "默认密码库",
    master_password: Optional[str] = None,
    keyfile_bytes: Optional[bytes] = None,
    recovery_key: Optional[str] = None,
    chunk_chars: int = CHUNK_CHARS,
) -> Dict[str, Any]:
    """生成双通道互补同步包：加密文件文本 + 配套二维码载荷（缺一不可）。"""
    payload = {
        "format": DUAL_MAGIC,
        "v": int(revision),
        "device": device,
        "vault_name": vault_name,
        "count": len(credentials),
        "credentials": [c.to_dict() for c in credentials],
    }
    plain = crypto.json_dumps(payload)
    checksum = hashlib.sha256(plain).hexdigest()
    compressed = zlib.compress(plain, 9)

    transfer_key = os.urandom(crypto.KEY_LEN)
    blob = crypto.encrypt(transfer_key, compressed, aad=DUAL_MAGIC.encode("ascii"))
    body = {"checksum": checksum, "blob": blob}
    encoded = crypto.b64u_encode(crypto.json_dumps(body))
    bundle_id = hashlib.sha256(encoded.encode("ascii")).hexdigest()[:12].upper()

    chunks = [encoded[i : i + chunk_chars] for i in range(0, len(encoded), chunk_chars)] or [""]
    total = len(chunks)
    chunk_lines = [f"{DUAL_MAGIC}-{bundle_id}-{i + 1:02d}/{total:02d}-{chunk}" for i, chunk in enumerate(chunks)]

    head = [
        "# PMVault 双通道加密同步文件（v2）",
        f"# 保险库：{vault_name} · 版本 v{revision} · 条目 {len(credentials)} 条 · 配对号 {bundle_id}",
        f"# 导出设备：{device or '(未知)'} · 导出于 {datetime.datetime.now():%Y-%m-%d %H:%M}",
        "#",
        "# 本文件不含任何密钥，必须与「配套互补二维码」同时提供才能解开，缺一不可。",
        "",
    ]
    file_text = "\n".join(head + chunk_lines) + "\n"

    qr_obj: Dict[str, Any] = {
        "v": 2,
        "id": bundle_id,
        "k": crypto.b64u_encode(transfer_key),
        "n": vault_name,
    }
    if master_password:
        qr_obj["mp"] = master_password
    if keyfile_bytes:
        qr_obj["kf"] = crypto.b64u_encode(keyfile_bytes)
    if recovery_key:
        qr_obj["rk"] = crypto.normalize_recovery_key(recovery_key)

    qr_payload = QR_PREFIX + crypto.b64u_encode(crypto.json_dumps(qr_obj))
    return {
        "bundle_id": bundle_id,
        "file_text": file_text,
        "qr_payload": qr_payload,
        "chunks": total,
        "filename": default_filename(revision, device, vault_name),
    }


def save_dual_channel_file(path: str, file_text: str) -> str:
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = abs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(file_text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, abs_path)
    return abs_path


def parse_dual_qr(qr_payload: str) -> Dict[str, Any]:
    s = (qr_payload or "").strip()
    if not s.startswith(QR_PREFIX):
        raise SyncError("互补二维码格式不正确（应以 PMQR2: 开头）")
    try:
        obj = crypto.json_loads(crypto.b64u_decode(s[len(QR_PREFIX) :]))
    except Exception as exc:
        raise SyncError("互补二维码内容损坏，无法解析") from exc
    if not isinstance(obj, dict) or "id" not in obj or "k" not in obj:
        raise SyncError("互补二维码缺少必要密钥字段")
    try:
        transfer_key = crypto.b64u_decode(str(obj["k"]))
    except Exception as exc:
        raise SyncError("互补二维码中的传输密钥损坏") from exc
    kf_bytes: Optional[bytes] = None
    if obj.get("kf"):
        try:
            kf_bytes = crypto.b64u_decode(str(obj["kf"]))
        except Exception as exc:
            raise SyncError("互补二维码中的本机密钥损坏") from exc
    return {
        "bundle_id": str(obj["id"]).upper(),
        "transfer_key": transfer_key,
        "vault_name": str(obj.get("n") or "导入的密码库"),
        "master_password": str(obj["mp"]) if obj.get("mp") else None,
        "keyfile_bytes": kf_bytes,
        "recovery_key": str(obj["rk"]) if obj.get("rk") else None,
    }


def unpack_dual_channel(lines: List[str], qr_payload: str) -> Dict[str, Any]:
    """合并加密文件与互补二维码解包（缺一不可、严格配对）。"""
    if not lines:
        raise SyncError("缺少加密同步文件")
    if not (qr_payload or "").strip():
        raise SyncError("缺少配套互补二维码")

    qr = parse_dual_qr(qr_payload)
    parsed: Dict[int, str] = {}
    total: Optional[int] = None
    file_bundle_id: Optional[str] = None

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            head, rest = line.split("-", 1)
            if head != DUAL_MAGIC:
                raise ValueError
            bid, rest2 = rest.split("-", 1)
            index_part, rest3 = rest2.split("/", 1)
            total_part, chunk_body = rest3.split("-", 1)
            idx = int(index_part)
            tot = int(total_part)
        except Exception as exc:
            raise SyncError("双通道同步文件格式不正确") from exc
        bid = bid.upper()
        if total is None:
            total, file_bundle_id = tot, bid
        elif total != tot or file_bundle_id != bid:
            raise SyncError("同步文件分片不一致（来自不同导出包）")
        parsed[idx] = parsed.get(idx, "") + chunk_body

    if total is None or file_bundle_id is None:
        raise SyncError("加密同步文件中没有有效分片")
    if file_bundle_id != qr["bundle_id"]:
        raise SyncError(f"文件与二维码不匹配（文件配对号 {file_bundle_id}，二维码配对号 {qr['bundle_id']}）")

    missing = [i for i in range(1, total + 1) if i not in parsed]
    if missing:
        raise MissingChunksError(missing, total)

    encoded = "".join(parsed[i] for i in range(1, total + 1))
    actual_id = hashlib.sha256(encoded.encode("ascii")).hexdigest()[:12].upper()
    if actual_id != qr["bundle_id"]:
        raise SyncChecksumError("同步文件完整性校验失败：文件可能被修改")

    try:
        body = crypto.json_loads(crypto.b64u_decode(encoded))
        compressed = crypto.decrypt(qr["transfer_key"], body["blob"], aad=DUAL_MAGIC.encode("ascii"))
        plain = zlib.decompress(compressed)
    except crypto.TamperError as exc:
        raise SyncError("解密失败：密钥不匹配或文件被篡改") from exc
    except Exception as exc:
        raise SyncError(f"同步数据无法解析：{exc}") from exc

    if hashlib.sha256(plain).hexdigest() != body.get("checksum"):
        raise SyncChecksumError("校验和不匹配：数据不完整或被修改")

    data = crypto.json_loads(plain)
    data["credentials"] = [Credential.from_dict(c) for c in data.get("credentials", [])]
    data["vault_name"] = data.get("vault_name") or qr["vault_name"]
    data["master_password"] = qr["master_password"]
    data["keyfile_bytes"] = qr["keyfile_bytes"]
    data["recovery_key"] = qr["recovery_key"]
    data["bundle_id"] = file_bundle_id
    return data


def unpack_dual_channel_file(path: str, qr_payload: str) -> Dict[str, Any]:
    return unpack_dual_channel(load_lines(path), qr_payload)


# ------------------------------------------------------------------ 旧版 PM1 只读兼容导入

def _sync_key(passphrase: str, salt: bytes) -> bytes:
    return crypto.derive_kek(passphrase, salt, SYNC_KDF)


def unpack(lines: List[str], passphrase: str) -> Dict[str, Any]:
    """兼容导入旧版 PM1 口令同步文件/分片字符串（只读兼容，不再提供旧版导出）。"""
    parsed: Dict[int, str] = {}
    total: Optional[int] = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            head, chunk = line.split("-", 1)
            if head != LEGACY_MAGIC:
                raise ValueError
            _ver, rest = chunk.split("-", 1)
            index_part, rest2 = rest.split("/", 1)
            total_part, chunk_body = rest2.split("-", 1)
            idx = int(index_part)
            tot = int(total_part)
        except Exception as exc:
            raise SyncError("旧版同步字符串格式不正确（应形如 PM1-3-01/03-xxxx）") from exc
        if total is None:
            total = tot
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
        compressed = crypto.decrypt(key, body["blob"], aad=LEGACY_MAGIC.encode())
        plain = zlib.decompress(compressed)
    except crypto.TamperError as exc:
        raise SyncError("同步口令错误或数据被篡改") from exc
    except Exception as exc:
        raise SyncError(f"同步数据无法解析：{exc}") from exc

    if hashlib.sha256(plain).hexdigest() != body.get("checksum"):
        raise SyncChecksumError("校验和不匹配：数据不完整或被修改")

    data = crypto.json_loads(plain)
    data["credentials"] = [Credential.from_dict(c) for c in data.get("credentials", [])]
    data["legacy_pm1"] = True
    return data


def load_lines(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def unpack_file(path: str, passphrase: str) -> Dict[str, Any]:
    return unpack(load_lines(path), passphrase)


def resolve_conflict(local_revision: int, remote_revision: int, local_device: str, remote_device: str) -> str:
    if remote_revision == local_revision and remote_device == local_device:
        return "none"
    if remote_revision > local_revision:
        return "pull"
    if remote_revision < local_revision:
        return "push"
    return "conflict" if local_device != remote_device else "none"


def default_filename(revision: int, device: str = "", vault_name: str = "") -> str:
    tag = "".join(ch for ch in (vault_name or device or "") if ch.isalnum())[:12]
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    parts = ["pmvault-sync", f"v{int(revision)}"]
    if tag:
        parts.append(tag)
    parts.append(stamp)
    return "-".join(parts) + FILE_SUFFIX
