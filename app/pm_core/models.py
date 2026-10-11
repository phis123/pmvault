"""数据模型：条目与保险库文件头。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

FORMAT_NAME = "pmvault"
FORMAT_VERSION = 1


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
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    custom_fields: List[Dict[str, Any]] = field(default_factory=list)
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
        self.tags = [t.strip() for t in self.tags if t and t.strip()]

    def touch(self, password_changed: bool = False) -> None:
        self.updated_at = now_iso()
        if password_changed:
            self.password_updated_at = now_iso()

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Credential":
        known = {f: raw.get(f) for f in cls.__dataclass_fields__ if f in raw}
        obj = cls(**known)
        obj.tags = list(obj.tags or [])
        obj.custom_fields = list(obj.custom_fields or [])
        return obj

    # ------------------------------------------------------------ 辅助
    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        haystack = " ".join([self.name, self.username, self.url, self.notes, " ".join(self.tags)]).lower()
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
    kdf: Dict[str, Any] = field(default_factory=dict)
    salt: str = ""
    auth_hash: str = ""
    payload: Dict[str, str] = field(default_factory=dict)  # 主密码加密的数据 blobo
    recovery_salt: str = ""
    recovery: Optional[Dict[str, str]] = None  # 恢复密钥包裹的 master_key
    revision: int = 0
    device: str = ""
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "VaultHeader":
        known = {f: raw.get(f) for f in cls.__dataclass_fields__ if f in raw}
        return cls(**known)
