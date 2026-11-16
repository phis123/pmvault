"""加密同步：把整个保险库压成「分片字符串」，可在任意端上传/取回。

格式：PM1-<版本>-<序号>/<总片数>-<Base64URL 密文>
口令与主密码分离：同步口令 → KEK_sync；服务端只能看到密文。

除了字符串，还支持**导出/导入文件**（见 export_text / save_to_file / unpack_file）：
文件内容 = 若干行 `#` 开头的说明 + 同样的分片行。整篇仍是密文，没有同步口令打不开。
字符串太长不方便搬运（几十片、几千字符），走文件更好用，也方便"另存到哪/发给谁"。
"""

from __future__ import annotations

import datetime
import hashlib
import os
import zlib
from typing import Any, Dict, List, Optional

from . import crypto
from .models import Credential

MAGIC = "PM1"
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
        if not line or line.startswith("#"):   # 空行与说明行（导出文件里的头部）跳过
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


# ------------------------------------------------------------------ 文件导出 / 导入
#
# 文件格式（UTF-8，行尾 \n）：
#   # 若干行说明（unpack 会跳过 # 开头与空行）
#   PM1-<版本>-01/NN-<密文>
#   PM1-<版本>-02/NN-<密文>
#   ...
# 刻意与"字符串"同构：字符串可以直接另存为文件，文件也能整篇粘回字符串框。

def header_lines(revision: int, device: str, credential_count: int, chunks: int) -> List[str]:
    return [
        "# PMVault 加密同步文件",
        f"# 本机版本 v{revision} · 条目 {credential_count} 条 · 分片 {chunks} 片",
        f"# 导出设备 {device or '(未知)'} · 导出于 {datetime.datetime.now():%Y-%m-%d %H:%M}",
        "#",
        "# 整篇都是密文：没有「同步口令」谁也解不开（连本程序也解不开）。",
        "# 导入方法：在另一台设备打开「加密同步 → 从文件导入」，选这个文件并输入同一个同步口令。",
        "# 口令一旦忘记，这个文件就永远打不开了——请与口令分开妥善保管。",
        "",
    ]


def export_text(credentials: List[Credential], passphrase: str, revision: int, device: str,
                chunk_chars: int = CHUNK_CHARS) -> str:
    """整包导出为可直接写文件的文本（说明行 + 分片行）。"""
    lines = pack(credentials, passphrase, revision, device, chunk_chars)
    head = header_lines(int(revision), device, len(credentials), len(lines))
    return "\n".join(head + lines) + "\n"


def save_to_file(path: str, credentials: List[Credential], passphrase: str,
                 revision: int, device: str, chunk_chars: int = CHUNK_CHARS) -> str:
    """导出到文件，返回实际写入的绝对路径。父目录不存在时自动创建。"""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    text = export_text(credentials, passphrase, revision, device, chunk_chars)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def load_lines(path: str) -> List[str]:
    """读入导出文件的所有行（含说明行；unpack 会自行跳过）。"""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def unpack_file(path: str, passphrase: str) -> Dict[str, Any]:
    """直接从文件还原。"""
    return unpack(load_lines(path), passphrase)


def default_filename(revision: int, device: str = "") -> str:
    """给「另存为」对话框用的默认文件名。"""
    tag = "".join(ch for ch in (device or "") if ch.isalnum())[:8]
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    parts = ["pmvault-sync", f"v{int(revision)}"]
    if tag:
        parts.append(tag)
    parts.append(stamp)
    return "-".join(parts) + FILE_SUFFIX


def looks_like_sync_file(text: str) -> bool:
    """粗判一段文本是不是同步包（用于导入前给出更友好的提示）。"""
    return any(line.strip().startswith(MAGIC + "-") for line in text.splitlines()[:200])
