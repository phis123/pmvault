"""保险库：本地单文件读写、条目增删改查、改主密码、恢复密钥。

存储格式（磁盘上永远是密文）：
  { "header": {...}, "payload": {"nonce": ..., "data": ...} }
其中 payload 解密后是 NDJSON：
  第 1 行  {"format":"pmvault","version":1,"count":N}
  之后每行   一个 Credential 的 JSON
这样小改动只需改一行，不需要每次全量重新序列化；磁盘上也不会出现明文。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from . import crypto, sorting
from .models import Credential, VaultHeader, FORMAT_NAME, FORMAT_VERSION, now_iso

# 恢复密钥用固定强度较低的 KDF：恢复密钥本身是高熵随机串，不需要抗字典攻击
RECOVERY_KDF = {"algo": "scrypt", "n": 1 << 13, "r": 8, "p": 1}
RECOVERY_AAD = b"pmvault-recovery"


class VaultError(Exception):
    pass


class VaultLockedError(VaultError):
    pass


class VaultFormatError(VaultError):
    pass


class Vault:
    """内存中的保险库；所有落盘内容都经过 AES-256-GCM。"""

    def __init__(self, path: str, header: VaultHeader, credentials: List[Credential], master_key: bytes) -> None:
        self.path = path
        self.header = header
        self.credentials = credentials
        self.last_recovery_key: Optional[str] = None
        self._master_key = master_key
        self._enc_key = crypto.subkey(master_key, "vault-enc")

    # ------------------------------------------------------------ 创建 / 解锁
    @classmethod
    def create(cls, path: str, password: str, device: str = "pc") -> "Vault":
        if os.path.exists(path):
            raise VaultError(f"文件已存在：{path}")
        salt = crypto.new_salt()
        kdf = crypto.default_kdf_params()
        master_key = crypto.derive_kek(password, salt, kdf)
        header = VaultHeader(
            format=FORMAT_NAME,
            version=FORMAT_VERSION,
            kdf=kdf,
            salt=crypto.b64e(salt),
            auth_hash=crypto.b64e(crypto.auth_hash(master_key)),
            recovery_salt=crypto.b64e(crypto.new_salt()),
            revision=0,
            device=device,
        )
        vault = cls(path, header, [], master_key)
        vault._rotate_recovery()          # 生成恢复密钥，UI 负责展示一次
        vault.save()
        return vault

    @classmethod
    def unlock(cls, path: str, password: str) -> "Vault":
        header, payload = cls._read_file(path)
        master_key = crypto.derive_kek(password, crypto.b64d(header.salt), header.kdf)
        if not crypto.verify_auth(master_key, crypto.b64d(header.auth_hash)):
            raise crypto.WrongPasswordError("主密码错误")
        creds = cls._decode_payload(crypto.decrypt(crypto.subkey(master_key, "vault-enc"), payload, aad=_aad(header)))
        return cls(path, header, creds, master_key)

    @classmethod
    def unlock_with_recovery_key(cls, path: str, recovery_key: str) -> "Vault":
        header, payload = cls._read_file(path)
        if not header.recovery:
            raise VaultError("该保险库没有设置恢复密钥")
        rk = crypto.derive_kek(
            crypto.normalize_recovery_key(recovery_key), crypto.b64d(header.recovery_salt), RECOVERY_KDF
        )
        try:
            master_key = crypto.decrypt(rk, header.recovery, aad=RECOVERY_AAD)
        except crypto.TamperError as exc:
            raise crypto.WrongPasswordError("恢复密钥错误") from exc
        creds = cls._decode_payload(crypto.decrypt(crypto.subkey(master_key, "vault-enc"), payload, aad=_aad(header)))
        return cls(path, header, creds, master_key)

    # ------------------------------------------------------------ 文件读写
    @staticmethod
    def _read_file(path: str) -> Tuple[VaultHeader, Dict[str, str]]:
        if not os.path.exists(path):
            raise VaultError(f"找不到保险库文件：{path}")
        try:
            raw = crypto.json_loads(open(path, "rb").read())
        except Exception as exc:
            raise VaultFormatError("保险库文件损坏，无法解析") from exc
        if not isinstance(raw, dict) or "header" not in raw or "payload" not in raw:
            raise VaultFormatError("不是本程序生成的保险库文件")
        header = VaultHeader.from_dict(raw["header"])
        if header.format != FORMAT_NAME:
            raise VaultFormatError("不是本程序生成的保险库文件")
        return header, raw["payload"]

    @staticmethod
    def _decode_payload(plain: bytes) -> List[Credential]:
        lines = plain.decode("utf-8").splitlines()
        creds: List[Credential] = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            try:
                creds.append(Credential.from_dict(crypto.json_loads(line.encode("utf-8"))))
            except Exception as exc:
                raise VaultFormatError("条目数据损坏") from exc
        return creds

    def _encode_payload(self) -> bytes:
        head = {"format": FORMAT_NAME, "version": FORMAT_VERSION, "count": len(self.credentials)}
        parts = [crypto.json_dumps(head).decode("utf-8")]
        for c in self.credentials:
            parts.append(crypto.json_dumps(c.to_dict()).decode("utf-8"))
        return ("\n".join(parts) + "\n").encode("utf-8")

    def save(self) -> None:
        """保存：整体重新加密（O(n)），每次换新 nonce。"""
        if not self._master_key:
            raise VaultLockedError("保险库已锁定")
        self.header.revision += 1
        self.header.updated_at = now_iso()
        plain = self._encode_payload()
        doc = {
            "header": self.header.to_dict(),
            "payload": crypto.encrypt(self._enc_key, plain, aad=_aad(self.header)),
            "payload_sha256": hashlib.sha256(plain).hexdigest(),
        }
        self._write_atomic(doc)

    def _write_atomic(self, doc: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(_dump(doc))
        if os.path.exists(self.path):
            shutil.copy2(self.path, self.path + ".bak")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ 条目
    def add(self, cred: Credential) -> Credential:
        cred.validate()
        self.credentials.append(cred)
        self.save()
        return cred

    def update(self, cred: Credential) -> Credential:
        cred.validate()
        for i, existing in enumerate(self.credentials):
            if existing.id == cred.id:
                password_changed = existing.password != cred.password
                cred.created_at = existing.created_at
                cred.touch(password_changed=password_changed)
                self.credentials[i] = cred
                self.save()
                return cred
        raise VaultError("找不到要修改的条目")

    def delete(self, cred_id: str) -> None:
        before = len(self.credentials)
        self.credentials = [c for c in self.credentials if c.id != cred_id]
        if len(self.credentials) == before:
            raise VaultError("找不到要删除的条目")
        self.save()

    def get(self, cred_id: str) -> Optional[Credential]:
        return next((c for c in self.credentials if c.id == cred_id), None)

    def search(self, query: str, sort_key: str = sorting.DEFAULT_SORT_KEY,
               sort_desc: bool = sorting.DEFAULT_SORT_DESC) -> List[Credential]:
        """按查询过滤后排序；默认创建时间递增（与旧版的插入顺序等价）。"""
        hits = [c for c in self.credentials if c.matches(query)]
        return sorting.sort_credentials(hits, sort_key, sort_desc)

    def all(self) -> List[Credential]:
        return sorted(self.credentials, key=lambda c: c.name.lower())

    # ------------------------------------------------------------ 主密码 / 恢复密钥
    def change_master_password(self, old_password: str, new_password: str) -> None:
        master_key = crypto.derive_kek(old_password, crypto.b64d(self.header.salt), self.header.kdf)
        if not crypto.verify_auth(master_key, crypto.b64d(self.header.auth_hash)):
            raise crypto.WrongPasswordError("原主密码错误")
        salt = crypto.new_salt()
        kdf = crypto.default_kdf_params()
        self._master_key = crypto.derive_kek(new_password, salt, kdf)
        self._enc_key = crypto.subkey(self._master_key, "vault-enc")
        self.header.salt = crypto.b64e(salt)
        self.header.kdf = kdf
        self.header.auth_hash = crypto.b64e(crypto.auth_hash(self._master_key))
        self.header.recovery_salt = crypto.b64e(crypto.new_salt())
        self._rotate_recovery()
        self.save()

    def _rotate_recovery(self) -> str:
        key_text = crypto.new_recovery_key()
        rk = crypto.derive_kek(
            crypto.normalize_recovery_key(key_text), crypto.b64d(self.header.recovery_salt), RECOVERY_KDF
        )
        self.header.recovery = crypto.encrypt(rk, self._master_key, aad=RECOVERY_AAD)
        self.last_recovery_key = key_text
        return key_text

    def rotate_recovery_key(self) -> str:
        """重新生成恢复密钥并保存（旧恢复密钥立即失效）。"""
        key_text = self._rotate_recovery()
        self.save()
        return key_text

    def lock(self) -> None:
        self._master_key = b""
        self._enc_key = b""
        self.credentials = []

    # ------------------------------------------------------------ 导出 / 统计
    def export_encrypted(self, target_path: str) -> None:
        """导出加密副本（仍是密文，需原主密码解锁）。"""
        self.save()
        directory = os.path.dirname(os.path.abspath(target_path)) or "."
        os.makedirs(directory, exist_ok=True)
        shutil.copy2(self.path, target_path)

    def stats(self) -> Dict[str, Any]:
        return {
            "count": len(self.credentials),
            "revision": self.header.revision,
            "device": self.header.device,
            "updated_at": self.header.updated_at,
            "kdf": self.header.kdf.get("algo", "?"),
            "weak": sum(1 for c in self.credentials if crypto.password_strength(c.password)["score"] <= 1),
            "old": sum(1 for c in self.credentials if c.age_days() > 180),
            "duplicates": self.duplicate_groups(),
        }

    def duplicate_groups(self) -> List[List[str]]:
        """返回重复密码的条目名分组（管理增强用）。"""
        buckets: Dict[str, List[str]] = {}
        for c in self.credentials:
            if c.password:
                buckets.setdefault(hashlib.sha256(c.password.encode("utf-8")).hexdigest(), []).append(c.name)
        return [names for names in buckets.values() if len(names) > 1]


def _aad(header: VaultHeader) -> bytes:
    """附加认证数据只依赖格式与版本，不依赖会被改写的字段（避免自引用）。"""
    return f"{header.format}|v{header.version}".encode("utf-8")


def _dump(doc: Dict[str, Any]) -> str:
    import json
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
