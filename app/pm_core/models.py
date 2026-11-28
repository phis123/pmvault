"""数据模型：条目（Credential）与保险库文件头（VaultHeader）。

版本演进：
- v1（旧版 1.0.x）：基础字段 name/username/password/url/notes/tags/custom_fields。
- v2（新版统一端）：扩展 totp_secret、recovery_phone、recovery_email、
  recovery_items（密保问答/备用恢复码组/自定义兜底）、api_keys（账号密钥）、
  favorite、password_history、deleted_at（回收站）；文件头增加 vault_name、
  keyfile_required/keyfile_id、recovery_version。
- 读取时 100% 兼容 v1，缺省字段自动补空值；导出与落盘统一为 v2。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

FORMAT_NAME = "pmvault"
FORMAT_VERSION = 2
LEGACY_FORMAT_VERSION = 1
MAX_PASSWORD_HISTORY = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Credential:
    id: str = field(default_factory=new_id)
    name: str = ""
    username: str = ""
    password: str = ""
    url: str = ""
    # 2FA (TOTP)：支持 Base32 密钥或 otpauth://totp/... URI
    totp_secret: str = ""
    # 密保联络
    recovery_phone: str = ""
    recovery_email: str = ""
    # 其他兜底：
    #   {"type": "qa", "question": "...", "answer": "..."}
    #   {"type": "backup_codes", "label": "Google 10组备用码", "codes": [{"code": "12345678", "used": False}]}
    #   {"type": "text", "label": "...", "value": "..."}
    recovery_items: List[Dict[str, Any]] = field(default_factory=list)
    # 账号相关密钥（如 GitHub Token / API Key）：
    #   {"label": "GitHub PAT", "value": "ghp_...", "note": "..."}
    api_keys: List[Dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    favorite: bool = False
    password_history: List[Dict[str, str]] = field(default_factory=list)
    # 保留旧版 custom_fields 字段用于无损兼容
    custom_fields: List[Dict[str, Any]] = field(default_factory=list)
    deleted_at: Optional[str] = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    password_updated_at: str = field(default_factory=now_iso)

    # ------------------------------------------------------------ 校验
    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("名称不能为空")
        if len(self.name) > 200:
            raise ValueError("名称过长（上限 200 字符）")
        for f in self.custom_fields:
            if not isinstance(f, dict) or "label" not in f:
                raise ValueError("自定义字段格式错误")
        for k in self.api_keys:
            if not isinstance(k, dict) or "label" not in k:
                raise ValueError("账号密钥格式错误")
        for item in self.recovery_items:
            if not isinstance(item, dict) or "type" not in item:
                raise ValueError("兜底信息格式错误")
        self.tags = [t.strip() for t in self.tags if t and t.strip()]

    def touch(self, password_changed: bool = False, previous_password: Optional[str] = None) -> None:
        self.updated_at = now_iso()
        if password_changed:
            if previous_password:
                self.password_history.insert(
                    0, {"password": previous_password, "changed_at": self.password_updated_at or self.updated_at}
                )
                self.password_history = self.password_history[:MAX_PASSWORD_HISTORY]
            self.password_updated_at = now_iso()

    @property
    def is_deleted(self) -> bool:
        return bool(self.deleted_at)

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Credential":
        known = {f: raw.get(f) for f in cls.__dataclass_fields__ if f in raw}
        obj = cls(**known)
        obj.tags = list(obj.tags or [])
        obj.custom_fields = list(obj.custom_fields or [])
        obj.recovery_items = list(obj.recovery_items or [])
        obj.api_keys = list(obj.api_keys or [])
        obj.password_history = list(obj.password_history or [])
        obj.favorite = bool(obj.favorite)
        obj.totp_secret = str(obj.totp_secret or "")
        obj.recovery_phone = str(obj.recovery_phone or "")
        obj.recovery_email = str(obj.recovery_email or "")
        return obj

    # ------------------------------------------------------------ 辅助
    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        extra: List[str] = []
        for k in self.api_keys:
            extra.append(str(k.get("label", "")))
            extra.append(str(k.get("note", "")))
        for r in self.recovery_items:
            extra.append(str(r.get("label", "")))
            extra.append(str(r.get("question", "")))
        haystack = " ".join(
            [
                self.name,
                self.username,
                self.url,
                self.notes,
                self.recovery_phone,
                self.recovery_email,
                " ".join(self.tags),
                " ".join(extra),
            ]
        ).lower()
        return all(part in haystack for part in q.split())

    def age_days(self) -> int:
        try:
            updated = datetime.fromisoformat(self.password_updated_at)
        except Exception:
            return 0
        return max(0, (datetime.now(timezone.utc).astimezone() - updated).days)


@dataclass
class VaultHeader:
    """明文文件头：只放非敏感元数据，方便版本与参数升级。"""

    format: str = FORMAT_NAME
    version: int = FORMAT_VERSION
    vault_name: str = "默认密码库"
    kdf: Dict[str, Any] = field(default_factory=dict)
    salt: str = ""
    auth_hash: str = ""
    keyfile_required: bool = False
    keyfile_id: str = ""
    recovery_salt: str = ""
    recovery: Optional[Dict[str, str]] = None  # 一次性高熵恢复码包裹的 master_key
    recovery_version: int = 1
    revision: int = 0
    device: str = ""
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "VaultHeader":
        known = {f: raw.get(f) for f in cls.__dataclass_fields__ if f in raw}
        obj = cls(**known)
        if not obj.vault_name:
            obj.vault_name = "默认密码库"
        obj.keyfile_required = bool(obj.keyfile_required)
        obj.keyfile_id = str(obj.keyfile_id or "")
        obj.recovery_version = int(obj.recovery_version or 1)
        return obj
