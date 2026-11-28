"""保险库：本地单文件读写、条目增删改查、回收站、本机密钥、一次性恢复码轮换、v1 自动升级。

存储格式（磁盘上永远是密文）：
  { "header": {...}, "payload": {"nonce": ..., "data": ...} }
其中 payload 解密后是 NDJSON：
  第 1 行  {"format":"pmvault","version":2,"count":N}
  之后每行   一个 Credential 的 JSON
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Any, Dict, List, Optional, Set, Tuple

from . import crypto, sorting
from .models import (
    Credential,
    FORMAT_NAME,
    FORMAT_VERSION,
    LEGACY_FORMAT_VERSION,
    VaultHeader,
    now_iso,
)

# 恢复密钥用固定强度较低的 KDF：恢复密钥本身是 ~120 bit 高熵随机串
RECOVERY_KDF = {"algo": "scrypt", "n": 1 << 13, "r": 8, "p": 1}
RECOVERY_AAD = b"pmvault-recovery"
MAX_ROLLING_BACKUPS = 3


class VaultError(Exception):
    pass


class VaultLockedError(VaultError):
    pass


class VaultFormatError(VaultError):
    pass


class VaultResetRequiredError(VaultError):
    """通过一次性恢复码登入后，必须先重设主密码并轮换恢复码。"""


class Vault:
    """内存中的保险库；所有落盘内容都经过 AES-256-GCM。"""

    def __init__(
        self,
        path: str,
        header: VaultHeader,
        credentials: List[Credential],
        master_key: bytes,
        *,
        migrated_from_v1: bool = False,
        must_reset_master_password: bool = False,
    ) -> None:
        self.path = path
        self.header = header
        self.credentials = credentials
        self.last_recovery_key: Optional[str] = None
        self._master_key = master_key
        self._enc_key = crypto.subkey(master_key, "vault-enc")
        # 标记：首次打开旧版 v1 库，待确认后自动升级为 v2 并签发全新一次性恢复码
        self.migrated_from_v1 = migrated_from_v1
        # 标记：通过一次性恢复码登入，必须先重设主密码并作废旧恢复码
        self.must_reset_master_password = must_reset_master_password

    # ------------------------------------------------------------ 元数据预览（无需解锁）
    @classmethod
    def peek_header(cls, path: str) -> VaultHeader:
        header, _ = cls._read_file(path)
        return header

    # ------------------------------------------------------------ 创建 / 解锁
    @classmethod
    def create(
        cls,
        path: str,
        password: str,
        device: str = "pc",
        vault_name: str = "默认密码库",
        keyfile_bytes: Optional[bytes] = None,
    ) -> "Vault":
        if os.path.exists(path):
            raise VaultError(f"文件已存在：{path}")
        salt = crypto.new_salt()
        kdf = crypto.default_kdf_params()
        master_key = crypto.derive_kek(password, salt, kdf, keyfile_bytes=keyfile_bytes)
        kf_required = bool(keyfile_bytes)
        kf_id = crypto.keyfile_fingerprint(keyfile_bytes) if keyfile_bytes else ""
        header = VaultHeader(
            format=FORMAT_NAME,
            version=FORMAT_VERSION,
            vault_name=(vault_name or "默认密码库").strip() or "默认密码库",
            kdf=kdf,
            salt=crypto.b64e(salt),
            auth_hash=crypto.b64e(crypto.auth_hash(master_key)),
            keyfile_required=kf_required,
            keyfile_id=kf_id,
            recovery_salt=crypto.b64e(crypto.new_salt()),
            recovery_version=1,
            revision=0,
            device=device,
        )
        vault = cls(path, header, [], master_key)
        vault._rotate_recovery()
        vault.save()
        return vault

    @classmethod
    def unlock(cls, path: str, password: str, keyfile_bytes: Optional[bytes] = None) -> "Vault":
        header, payload = cls._read_file(path)
        if header.keyfile_required:
            if not keyfile_bytes:
                raise crypto.KeyfileRequiredError(header.keyfile_id)
            actual_id = crypto.keyfile_fingerprint(keyfile_bytes)
            if header.keyfile_id and actual_id != header.keyfile_id:
                raise crypto.KeyfileMismatchError(header.keyfile_id, actual_id)
            kf_for_kdf: Optional[bytes] = keyfile_bytes
        else:
            kf_for_kdf = None

        master_key = crypto.derive_kek(password, crypto.b64d(header.salt), header.kdf, keyfile_bytes=kf_for_kdf)
        if not crypto.verify_auth(master_key, crypto.b64d(header.auth_hash)):
            raise crypto.WrongPasswordError("主密码错误")
        creds = cls._decode_payload(
            crypto.decrypt(crypto.subkey(master_key, "vault-enc"), payload, aad=_aad(header))
        )
        is_legacy = int(header.version) < FORMAT_VERSION
        return cls(path, header, creds, master_key, migrated_from_v1=is_legacy)

    @classmethod
    def unlock_with_recovery_key(cls, path: str, recovery_key: str) -> "Vault":
        """用一次性高熵恢复码解锁（可同时绕过主密码与本机密钥），进入强制改密状态。"""
        header, payload = cls._read_file(path)
        if not header.recovery:
            raise VaultError("该保险库没有设置一次性恢复码")
        rk = crypto.derive_kek(
            crypto.normalize_recovery_key(recovery_key), crypto.b64d(header.recovery_salt), RECOVERY_KDF
        )
        try:
            master_key = crypto.decrypt(rk, header.recovery, aad=RECOVERY_AAD)
        except crypto.TamperError as exc:
            raise crypto.WrongPasswordError("一次性恢复码错误或已失效") from exc
        creds = cls._decode_payload(
            crypto.decrypt(crypto.subkey(master_key, "vault-enc"), payload, aad=_aad(header))
        )
        is_legacy = int(header.version) < FORMAT_VERSION
        return cls(
            path,
            header,
            creds,
            master_key,
            migrated_from_v1=is_legacy,
            must_reset_master_password=True,
        )

    # ------------------------------------------------------------ v1 → v2 单向升级 & 一次性恢复码强制改密
    def complete_v1_upgrade(
        self,
        password: str,
        keyfile_bytes: Optional[bytes] = None,
        vault_name: Optional[str] = None,
    ) -> str:
        """将已解锁的旧版 v1 保险库单向升级为 v2：
        1. 先保留原 v1 文件副本为 `.v1.bak`（若尚未备份）
        2. 升级格式版本至 v2、升级高强度 KDF、可选绑定本机密钥
        3. 换新 recovery_salt 并生成全新一次性恢复码（旧恢复码失效）
        4. 立即加密落盘，返回新的一次性恢复码（UI 需醒目提示保存）
        """
        self._ensure_unlocked()
        if os.path.exists(self.path):
            v1_backup = self.path + ".v1.bak"
            if not os.path.exists(v1_backup):
                shutil.copy2(self.path, v1_backup)

        if vault_name and vault_name.strip():
            self.header.vault_name = vault_name.strip()

        salt = crypto.new_salt()
        kdf = crypto.default_kdf_params()
        self._master_key = crypto.derive_kek(password, salt, kdf, keyfile_bytes=keyfile_bytes)
        self._enc_key = crypto.subkey(self._master_key, "vault-enc")
        self.header.version = FORMAT_VERSION
        self.header.salt = crypto.b64e(salt)
        self.header.kdf = kdf
        self.header.auth_hash = crypto.b64e(crypto.auth_hash(self._master_key))
        self.header.keyfile_required = bool(keyfile_bytes)
        self.header.keyfile_id = crypto.keyfile_fingerprint(keyfile_bytes) if keyfile_bytes else ""
        self.header.recovery_salt = crypto.b64e(crypto.new_salt())
        self.header.recovery_version = int(self.header.recovery_version or 1) + 1
        new_code = self._rotate_recovery()
        self.migrated_from_v1 = False
        self.must_reset_master_password = False
        self.save()
        return new_code

    def complete_recovery_reset(
        self,
        new_password: str,
        keyfile_bytes: Optional[bytes] = None,
    ) -> str:
        """一次性恢复码登入后的强制步骤：重设主密码、作废刚用过的旧恢复码、签发全新一次性恢复码。"""
        self._ensure_unlocked()
        if os.path.exists(self.path) and self.migrated_from_v1:
            v1_backup = self.path + ".v1.bak"
            if not os.path.exists(v1_backup):
                shutil.copy2(self.path, v1_backup)

        salt = crypto.new_salt()
        kdf = crypto.default_kdf_params()
        self._master_key = crypto.derive_kek(new_password, salt, kdf, keyfile_bytes=keyfile_bytes)
        self._enc_key = crypto.subkey(self._master_key, "vault-enc")
        self.header.version = FORMAT_VERSION
        self.header.salt = crypto.b64e(salt)
        self.header.kdf = kdf
        self.header.auth_hash = crypto.b64e(crypto.auth_hash(self._master_key))
        self.header.keyfile_required = bool(keyfile_bytes)
        self.header.keyfile_id = crypto.keyfile_fingerprint(keyfile_bytes) if keyfile_bytes else ""
        # 换新盐 + 生成新恢复码，刚用过的旧恢复码彻底失效
        self.header.recovery_salt = crypto.b64e(crypto.new_salt())
        self.header.recovery_version = int(self.header.recovery_version or 1) + 1
        new_code = self._rotate_recovery()
        self.must_reset_master_password = False
        self.migrated_from_v1 = False
        self.save()
        return new_code

    # ------------------------------------------------------------ 文件读写
    @staticmethod
    def _read_file(path: str) -> Tuple[VaultHeader, Dict[str, str]]:
        if not os.path.exists(path):
            raise VaultError(f"找不到保险库文件：{path}")
        try:
            with open(path, "rb") as fh:
                raw = crypto.json_loads(fh.read())
        except Exception as exc:
            raise VaultFormatError("保险库文件损坏，无法解析") from exc
        if not isinstance(raw, dict) or "header" not in raw or "payload" not in raw:
            raise VaultFormatError("不是本程序生成的保险库文件")
        header = VaultHeader.from_dict(raw["header"])
        if header.format != FORMAT_NAME:
            raise VaultFormatError("不是本程序生成的保险库文件")
        if header.version not in (LEGACY_FORMAT_VERSION, FORMAT_VERSION):
            raise VaultFormatError(f"不支持的保险库版本：v{header.version}")
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

    def _ensure_unlocked(self, allow_pending_reset: bool = True) -> None:
        if not self._master_key:
            raise VaultLockedError("保险库已锁定")
        if not allow_pending_reset and self.must_reset_master_password:
            raise VaultResetRequiredError("请先完成主密码重设与新一次性恢复码生成")

    def save(self) -> None:
        """保存：整体重新加密（强制以 v2 格式落盘），每次换新 nonce + fsync + 滚动备份。"""
        self._ensure_unlocked(allow_pending_reset=True)
        self.header.version = FORMAT_VERSION
        self.header.revision += 1
        self.header.updated_at = now_iso()
        plain = self._encode_payload()
        doc = {
            "header": self.header.to_dict(),
            "payload": crypto.encrypt(self._enc_key, plain, aad=_aad(self.header)),
        }
        self._write_atomic(doc)

    def _write_atomic(self, doc: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_dump(doc))
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(self.path):
            self._rotate_backups()
            shutil.copy2(self.path, self.path + ".bak")
        os.replace(tmp, self.path)

    def _rotate_backups(self) -> None:
        """滚动保留最近 MAX_ROLLING_BACKUPS 份本地加密备份（.bak, .bak.1, .bak.2）。"""
        for idx in range(MAX_ROLLING_BACKUPS - 1, 0, -1):
            src = f"{self.path}.bak" if idx == 1 else f"{self.path}.bak.{idx - 1}"
            dst = f"{self.path}.bak.{idx}"
            if os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass

    # ------------------------------------------------------------ 条目与回收站
    def active_credentials(self) -> List[Credential]:
        return [c for c in self.credentials if not c.is_deleted]

    def trash_credentials(self) -> List[Credential]:
        return [c for c in self.credentials if c.is_deleted]

    def add(self, cred: Credential) -> Credential:
        self._ensure_unlocked(allow_pending_reset=False)
        cred.validate()
        stored = Credential.from_dict(cred.to_dict())
        self.credentials.append(stored)
        self.save()
        return stored

    def update(self, cred: Credential) -> Credential:
        self._ensure_unlocked(allow_pending_reset=False)
        cred.validate()
        for i, existing in enumerate(self.credentials):
            if existing.id == cred.id:
                updated = Credential.from_dict(cred.to_dict())
                password_changed = existing.password != updated.password
                updated.created_at = existing.created_at
                if not updated.password_history and existing.password_history:
                    updated.password_history = list(existing.password_history)
                updated.touch(
                    password_changed=password_changed,
                    previous_password=existing.password if password_changed else None,
                )
                self.credentials[i] = updated
                cred.password_history = list(updated.password_history)
                cred.updated_at = updated.updated_at
                cred.password_updated_at = updated.password_updated_at
                self.save()
                return updated
        raise VaultError("找不到要修改的条目")

    def soft_delete(self, cred_id: str) -> None:
        """移入回收站（可恢复）。"""
        self._ensure_unlocked(allow_pending_reset=False)
        for c in self.credentials:
            if c.id == cred_id:
                c.deleted_at = now_iso()
                c.updated_at = c.deleted_at
                self.save()
                return
        raise VaultError("找不到要删除的条目")

    def restore(self, cred_id: str) -> None:
        """从回收站恢复条目。"""
        self._ensure_unlocked(allow_pending_reset=False)
        for c in self.credentials:
            if c.id == cred_id:
                c.deleted_at = None
                c.updated_at = now_iso()
                self.save()
                return
        raise VaultError("找不到要恢复的条目")

    def delete(self, cred_id: str) -> None:
        """彻底永久删除条目。"""
        self._ensure_unlocked(allow_pending_reset=False)
        before = len(self.credentials)
        self.credentials = [c for c in self.credentials if c.id != cred_id]
        if len(self.credentials) == before:
            raise VaultError("找不到要删除的条目")
        self.save()

    def empty_trash(self) -> int:
        self._ensure_unlocked(allow_pending_reset=False)
        before = len(self.credentials)
        self.credentials = [c for c in self.credentials if not c.is_deleted]
        removed = before - len(self.credentials)
        if removed:
            self.save()
        return removed

    def get(self, cred_id: str) -> Optional[Credential]:
        return next((c for c in self.credentials if c.id == cred_id), None)

    def search(
        self,
        query: str = "",
        sort_key: str = sorting.DEFAULT_SORT_KEY,
        sort_desc: bool = sorting.DEFAULT_SORT_DESC,
        *,
        category: str = "all",
        tag: str = "",
        old_days: int = 180,
    ) -> List[Credential]:
        """按分类、标签、关键字过滤并排序。

        category: 'all' | 'favorites' | 'weak' | 'duplicate' | 'old' | 'totp' | 'trash'
        """
        if category == "trash":
            pool = self.trash_credentials()
        else:
            pool = self.active_credentials()
            if category == "favorites":
                pool = [c for c in pool if c.favorite]
            elif category == "weak":
                pool = [c for c in pool if c.password and crypto.password_strength(c.password)["score"] <= 1]
            elif category == "duplicate":
                dup_hashes = self._duplicate_password_hashes()
                pool = [
                    c
                    for c in pool
                    if c.password and hashlib.sha256(c.password.encode("utf-8")).hexdigest() in dup_hashes
                ]
            elif category == "old":
                pool = [c for c in pool if c.age_days() > old_days]
            elif category == "totp":
                pool = [c for c in pool if (c.totp_secret or "").strip()]

        if tag:
            pool = [c for c in pool if tag in c.tags]

        hits = [c for c in pool if c.matches(query)]
        return sorting.sort_credentials(hits, sort_key, sort_desc)

    def all(self) -> List[Credential]:
        return sorted(self.active_credentials(), key=lambda c: c.name.lower())

    def all_tags(self) -> List[Tuple[str, int]]:
        counts: Dict[str, int] = {}
        for c in self.active_credentials():
            for t in c.tags:
                counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))

    # ------------------------------------------------------------ 主密码 / 本机密钥 / 一次性恢复码
    def change_master_password(
        self,
        old_password: str,
        new_password: str,
        *,
        old_keyfile_bytes: Optional[bytes] = None,
        new_keyfile_bytes: Optional[bytes] = None,
        keep_existing_keyfile: bool = True,
    ) -> str:
        """修改主密码（可选同步启用/更换/关闭本机密钥），并自动轮换一次性恢复码。"""
        self._ensure_unlocked(allow_pending_reset=False)
        kf_verify = old_keyfile_bytes if self.header.keyfile_required else None
        master_key = crypto.derive_kek(old_password, crypto.b64d(self.header.salt), self.header.kdf, keyfile_bytes=kf_verify)
        if not crypto.verify_auth(master_key, crypto.b64d(self.header.auth_hash)):
            raise crypto.WrongPasswordError("原主密码错误")

        target_kf = old_keyfile_bytes if (keep_existing_keyfile and new_keyfile_bytes is None and self.header.keyfile_required) else new_keyfile_bytes
        salt = crypto.new_salt()
        kdf = crypto.default_kdf_params()
        self._master_key = crypto.derive_kek(new_password, salt, kdf, keyfile_bytes=target_kf)
        self._enc_key = crypto.subkey(self._master_key, "vault-enc")
        self.header.salt = crypto.b64e(salt)
        self.header.kdf = kdf
        self.header.auth_hash = crypto.b64e(crypto.auth_hash(self._master_key))
        self.header.keyfile_required = bool(target_kf)
        self.header.keyfile_id = crypto.keyfile_fingerprint(target_kf) if target_kf else ""
        self.header.recovery_salt = crypto.b64e(crypto.new_salt())
        self.header.recovery_version = int(self.header.recovery_version or 1) + 1
        new_code = self._rotate_recovery()
        self.save()
        return new_code

    def _rotate_recovery(self) -> str:
        key_text = crypto.new_recovery_key()
        rk = crypto.derive_kek(
            crypto.normalize_recovery_key(key_text), crypto.b64d(self.header.recovery_salt), RECOVERY_KDF
        )
        self.header.recovery = crypto.encrypt(rk, self._master_key, aad=RECOVERY_AAD)
        self.last_recovery_key = key_text
        return key_text

    def rotate_recovery_key(self) -> str:
        """重新生成一次性恢复码并保存（旧恢复码立即失效）。"""
        self._ensure_unlocked(allow_pending_reset=False)
        self.header.recovery_salt = crypto.b64e(crypto.new_salt())
        self.header.recovery_version = int(self.header.recovery_version or 1) + 1
        key_text = self._rotate_recovery()
        self.save()
        return key_text

    def lock(self) -> None:
        self._master_key = b""
        self._enc_key = b""
        self.credentials = []
        self.last_recovery_key = None
        self.must_reset_master_password = False

    # ------------------------------------------------------------ 导出 / 统计 / 安全体检
    def export_encrypted(self, target_path: str) -> None:
        """导出新版 v2 加密副本（仍是密文）。"""
        self._ensure_unlocked(allow_pending_reset=False)
        self.save()
        directory = os.path.dirname(os.path.abspath(target_path)) or "."
        os.makedirs(directory, exist_ok=True)
        shutil.copy2(self.path, target_path)

    def _duplicate_password_hashes(self) -> Set[str]:
        counts: Dict[str, int] = {}
        for c in self.active_credentials():
            if c.password:
                h = hashlib.sha256(c.password.encode("utf-8")).hexdigest()
                counts[h] = counts.get(h, 0) + 1
        return {h for h, cnt in counts.items() if cnt > 1}

    def duplicate_groups(self) -> List[List[str]]:
        buckets: Dict[str, List[str]] = {}
        for c in self.active_credentials():
            if c.password:
                buckets.setdefault(hashlib.sha256(c.password.encode("utf-8")).hexdigest(), []).append(c.name)
        return [names for names in buckets.values() if len(names) > 1]

    def markers(self, cred: Credential, old_days: int = 180, dup_hashes: Optional[Set[str]] = None) -> List[Tuple[str, str]]:
        """返回条目健康标签列表 [(文字, 颜色HEX)]。"""
        flags: List[Tuple[str, str]] = []
        if cred.password and crypto.password_strength(cred.password)["score"] <= 1:
            flags.append(("弱", "#dc2626"))
        if cred.password:
            hashes = dup_hashes if dup_hashes is not None else self._duplicate_password_hashes()
            if hashlib.sha256(cred.password.encode("utf-8")).hexdigest() in hashes:
                flags.append(("重", "#d97706"))
        if cred.age_days() > old_days:
            flags.append(("旧", "#64748b"))
        return flags

    def stats(self, old_days: int = 180) -> Dict[str, Any]:
        active = self.active_credentials()
        dup_groups = self.duplicate_groups()
        dup_count = sum(len(g) for g in dup_groups)
        return {
            "count": len(active),
            "trash_count": len(self.trash_credentials()),
            "favorites": sum(1 for c in active if c.favorite),
            "totp_count": sum(1 for c in active if (c.totp_secret or "").strip()),
            "revision": self.header.revision,
            "device": self.header.device,
            "vault_name": self.header.vault_name,
            "updated_at": self.header.updated_at,
            "kdf": self.header.kdf.get("algo", "?"),
            "keyfile_required": self.header.keyfile_required,
            "keyfile_id": self.header.keyfile_id,
            "weak": sum(1 for c in active if c.password and crypto.password_strength(c.password)["score"] <= 1),
            "old": sum(1 for c in active if c.age_days() > old_days),
            "duplicate_count": dup_count,
            "duplicates": dup_groups,
        }


def _aad(header: VaultHeader) -> bytes:
    """附加认证数据依赖格式与版本（兼容 v1 读取与 v2 落盘）。"""
    return f"{header.format}|v{header.version}".encode("utf-8")


def _dump(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
