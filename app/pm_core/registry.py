"""多密码库注册表与本机密钥文件隔离路径管理。

设计要点：
- 登录前可列出并选择多个独立的密码库；
- 自动发现并兼容旧版 `data/vault.pmvault`（原地保留，绝不移动或破坏用户原数据）；
- 新增密码库存放在 `data/vaults/<vault_id>.pmvault`；
- 本机密钥文件（.pmkey）默认物理隔离存放于系统私有目录：
  * Windows: %LOCALAPPDATA%\\PMVault\\keys\\<vault_id>.pmkey
  * Android: <FLET_APP_STORAGE_DATA>/.device_keys/<vault_id>.pmkey
  * 其他平台: ~/.local/share/pmvault/keys/<vault_id>.pmkey
  不与 data/ 混放，避免打包备份 data/ 时把密钥和库文件一起泄露。
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from . import crypto
from .models import LEGACY_FORMAT_VERSION, VaultHeader, now_iso
from .vault import Vault


@dataclass
class VaultRecord:
    id: str
    name: str
    path: str
    keyfile_path: str = ""
    created_at: str = ""
    is_legacy_default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "VaultRecord":
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex[:12]),
            name=str(raw.get("name") or "默认密码库"),
            path=str(raw.get("path") or ""),
            keyfile_path=str(raw.get("keyfile_path") or ""),
            created_at=str(raw.get("created_at") or now_iso()),
            is_legacy_default=bool(raw.get("is_legacy_default", False)),
        )


def default_keyfile_dir() -> str:
    """返回与 vault data/ 物理隔离的本机密钥目录。"""
    flet_storage = os.environ.get("FLET_APP_STORAGE_DATA")
    if flet_storage:
        path = os.path.join(flet_storage, ".device_keys")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "PMVault", "keys")
    else:
        path = os.path.join(os.path.expanduser("~"), ".local", "share", "pmvault", "keys")
    os.makedirs(path, exist_ok=True)
    return path


class VaultRegistry:
    """管理本地所有密码库条目与元数据索引。"""

    def __init__(self, data_dir: str, keyfile_dir: Optional[str] = None) -> None:
        self.data_dir = os.path.abspath(data_dir)
        self.vaults_dir = os.path.join(self.data_dir, "vaults")
        self.keyfile_dir = os.path.abspath(keyfile_dir) if keyfile_dir else default_keyfile_dir()
        self.index_path = os.path.join(self.data_dir, "vaults_index.json")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.vaults_dir, exist_ok=True)
        os.makedirs(self.keyfile_dir, exist_ok=True)
        self._records: List[VaultRecord] = []
        self.reload()

    def reload(self) -> List[VaultRecord]:
        records: List[VaultRecord] = []
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as fh:
                    raw = json.loads(fh.read())
                if isinstance(raw, list):
                    records = [VaultRecord.from_dict(item) for item in raw if isinstance(item, dict)]
            except Exception:
                records = []

        # 自动兼容发现旧版默认位置：data/vault.pmvault（原地保留不移动）
        legacy_path = os.path.join(self.data_dir, "vault.pmvault")
        known_paths = {os.path.normcase(os.path.abspath(r.path)) for r in records if r.path}
        if os.path.exists(legacy_path) and os.path.normcase(os.path.abspath(legacy_path)) not in known_paths:
            name = "默认密码库"
            try:
                hdr = Vault.peek_header(legacy_path)
                if hdr.vault_name:
                    name = hdr.vault_name
            except Exception:
                pass
            records.insert(
                0,
                VaultRecord(
                    id="default",
                    name=name,
                    path=legacy_path,
                    created_at=now_iso(),
                    is_legacy_default=True,
                ),
            )
            self._records = records
            self._save()
        else:
            self._records = records
        return self._records

    def _save(self) -> None:
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps([r.to_dict() for r in self._records], ensure_ascii=False, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.index_path)

    def list_vaults(self) -> List[Dict[str, Any]]:
        """列出所有密码库及其文件头摘要（是否缺失、是否旧版 v1、是否需要本机密钥）。"""
        result: List[Dict[str, Any]] = []
        for rec in self._records:
            exists = os.path.exists(rec.path)
            hdr: Optional[VaultHeader] = None
            if exists:
                try:
                    hdr = Vault.peek_header(rec.path)
                except Exception:
                    hdr = None
            result.append(
                {
                    "id": rec.id,
                    "name": (hdr.vault_name if hdr and hdr.vault_name else rec.name),
                    "path": rec.path,
                    "exists": exists,
                    "keyfile_path": rec.keyfile_path,
                    "keyfile_exists": bool(rec.keyfile_path and os.path.exists(rec.keyfile_path)),
                    "keyfile_required": bool(hdr.keyfile_required) if hdr else False,
                    "keyfile_id": hdr.keyfile_id if hdr else "",
                    "version": hdr.version if hdr else 0,
                    "is_v1": bool(hdr and hdr.version == LEGACY_FORMAT_VERSION),
                    "updated_at": hdr.updated_at if hdr else rec.created_at,
                    "revision": hdr.revision if hdr else 0,
                }
            )
        return result

    def get(self, vault_id: str) -> Optional[VaultRecord]:
        return next((r for r in self._records if r.id == vault_id), None)

    def default_keyfile_path_for(self, vault_id: str) -> str:
        return os.path.join(self.keyfile_dir, f"{vault_id}{crypto.KEYFILE_SUFFIX}")

    def create_vault(
        self,
        name: str,
        password: str,
        *,
        device: str = "pc",
        use_keyfile: bool = True,
        keyfile_bytes: Optional[bytes] = None,
        custom_keyfile_path: Optional[str] = None,
    ) -> Tuple[VaultRecord, Vault, Optional[str]]:
        """新建一个独立密码库并注册，返回 (record, vault, keyfile_path_or_None)。"""
        vid = uuid.uuid4().hex[:10]
        clean_name = (name or "我的密码库").strip() or "我的密码库"
        vpath = os.path.join(self.vaults_dir, f"{vid}.pmvault")

        kf_bytes: Optional[bytes] = None
        kf_path = ""
        if use_keyfile:
            if keyfile_bytes is None:
                _, kf_bytes = crypto.new_keyfile()
            else:
                kf_bytes = keyfile_bytes
            kf_path = custom_keyfile_path or self.default_keyfile_path_for(vid)
            crypto.save_keyfile(kf_path, kf_bytes)

        vault = Vault.create(vpath, password, device=device, vault_name=clean_name, keyfile_bytes=kf_bytes)
        rec = VaultRecord(
            id=vid,
            name=clean_name,
            path=vpath,
            keyfile_path=kf_path,
            created_at=now_iso(),
        )
        self._records.append(rec)
        self._save()
        return rec, vault, (kf_path or None)

    def register_existing_file(
        self,
        src_path: str,
        *,
        name: Optional[str] = None,
        copy_into_managed: bool = True,
        keyfile_path: str = "",
    ) -> VaultRecord:
        """导入/挂载外部 .pmvault 文件（兼容旧版 v1 与新版 v2）到多库列表中。"""
        hdr = Vault.peek_header(src_path)
        vid = uuid.uuid4().hex[:10]
        display_name = (name or hdr.vault_name or os.path.splitext(os.path.basename(src_path))[0]).strip() or "导入的密码库"
        if copy_into_managed:
            dst = os.path.join(self.vaults_dir, f"{vid}.pmvault")
            shutil.copy2(src_path, dst)
            target_path = dst
        else:
            target_path = os.path.abspath(src_path)
        rec = VaultRecord(
            id=vid,
            name=display_name,
            path=target_path,
            keyfile_path=keyfile_path,
            created_at=now_iso(),
        )
        self._records.append(rec)
        self._save()
        return rec

    def set_keyfile_path(self, vault_id: str, keyfile_path: str) -> None:
        rec = self.get(vault_id)
        if rec is None:
            raise KeyError(f"未找到密码库：{vault_id}")
        rec.keyfile_path = keyfile_path
        self._save()

    def save_keyfile_for_vault(self, vault_id: str, raw_key: bytes, custom_path: Optional[str] = None) -> str:
        rec = self.get(vault_id)
        if rec is None:
            raise KeyError(f"未找到密码库：{vault_id}")
        target = custom_path or self.default_keyfile_path_for(vault_id)
        saved = crypto.save_keyfile(target, raw_key)
        rec.keyfile_path = saved
        self._save()
        return saved

    def rename_vault(self, vault_id: str, new_name: str) -> None:
        rec = self.get(vault_id)
        if rec is None:
            raise KeyError(f"未找到密码库：{vault_id}")
        rec.name = (new_name or rec.name).strip() or rec.name
        self._save()

    def remove_vault(self, vault_id: str, *, delete_file: bool = False) -> None:
        rec = self.get(vault_id)
        if rec is None:
            return
        self._records = [r for r in self._records if r.id != vault_id]
        self._save()
        if delete_file:
            for p in (rec.path, rec.path + ".bak", rec.path + ".bak.1", rec.path + ".bak.2"):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
