"""pm_core v2 核心与新旧兼容测试：
覆盖扩展数据模型、TOTP、本机密钥文件、一次性恢复码强制改密与作废、
v1 旧库自动升级、PM2 双通道互补同步、PM1 旧同步单向兼容、多密码库注册表、QR 生成。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import pytest

from pm_core import (
    Credential,
    FORMAT_NAME,
    FORMAT_VERSION,
    KeyfileMismatchError,
    KeyfileRequiredError,
    Vault,
    VaultHeader,
    VaultRegistry,
    VaultResetRequiredError,
    WrongPasswordError,
    crypto,
    qr,
    settings as settings_mod,
    sync,
    totp,
)


def _make_legacy_v1_vault(path: str, password: str, creds: list[dict]) -> str:
    """按 v1.0.4 格式构造一个真实旧版 v1 保险库文件，返回其旧恢复密钥。"""
    salt = crypto.new_salt()
    kdf = {"algo": "scrypt", "n": 1 << 13, "r": 8, "p": 1}
    mk = crypto.derive_kek(password, salt, kdf)
    rec_salt = crypto.new_salt()
    rec_key = crypto.new_recovery_key()
    rk = crypto.derive_kek(crypto.normalize_recovery_key(rec_key), rec_salt, {"algo": "scrypt", "n": 1 << 13, "r": 8, "p": 1})
    header = {
        "format": FORMAT_NAME,
        "version": 1,
        "kdf": kdf,
        "salt": crypto.b64e(salt),
        "auth_hash": crypto.b64e(crypto.auth_hash(mk)),
        "payload": {},
        "recovery_salt": crypto.b64e(rec_salt),
        "recovery": crypto.encrypt(rk, mk, aad=b"pmvault-recovery"),
        "revision": 3,
        "device": "legacy-pc",
        "updated_at": "2026-10-17T23:21:00+08:00",
    }
    head_line = crypto.json_dumps({"format": FORMAT_NAME, "version": 1, "count": len(creds)}).decode("utf-8")
    lines = [head_line] + [crypto.json_dumps(c).decode("utf-8") for c in creds]
    plain = ("\n".join(lines) + "\n").encode("utf-8")
    doc = {
        "header": header,
        "payload": crypto.encrypt(crypto.subkey(mk, "vault-enc"), plain, aad=b"pmvault|v1"),
        "payload_sha256": hashlib.sha256(plain).hexdigest(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    return rec_key


def _make_legacy_pm1_lines(creds: list[Credential], passphrase: str) -> list[str]:
    """按 v1.0.4 的 PM1 口令格式构造旧版同步文件行。"""
    payload = {
        "v": 5,
        "device": "old-android",
        "count": len(creds),
        "credentials": [c.to_dict() for c in creds],
    }
    plain = crypto.json_dumps(payload)
    checksum = hashlib.sha256(plain).hexdigest()
    compressed = zlib.compress(plain, 9)
    salt = crypto.new_salt()
    key = crypto.derive_kek(passphrase, salt, sync.SYNC_KDF)
    body = {"salt": crypto.b64e(salt), "checksum": checksum, "blob": crypto.encrypt(key, compressed, aad=b"PM1")}
    encoded = crypto.b64u_encode(crypto.json_dumps(body))
    chunks = [encoded[i : i + 400] for i in range(0, len(encoded), 400)]
    total = len(chunks)
    return ["# legacy header", ""] + [f"PM1-5-{i+1:02d}/{total:02d}-{c}" for i, c in enumerate(chunks)]


def test_extended_credential_and_trash_and_history():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "v.pmvault")
        v = Vault.create(p, "StrongPass!99", vault_name="日常库")
        c = Credential(
            name="GitHub",
            url="https://github.com",
            username="phis123",
            password="InitialPassword!1",
            totp_secret="JBSWY3DPEHPK3PXP",
            recovery_phone="13800138000",
            recovery_email="backup@example.com",
            recovery_items=[
                {"type": "qa", "question": "第一只宠物？", "answer": "旺财"},
                {
                    "type": "backup_codes",
                    "label": "Google 10组备用码",
                    "codes": [{"code": "1234 5678", "used": False}, {"code": "8765 4321", "used": True}],
                },
                {"type": "text", "label": "其他兜底", "value": "备用邮箱2"},
            ],
            api_keys=[
                {"label": "GitHub PAT", "value": "ghp_secret_value_123", "note": "仅用于 pmvault"},
            ],
            notes="主力开发账号",
            tags=["开发", "高频"],
            favorite=True,
        )
        v.add(c)

        # 修改密码自动写入密码历史
        c.password = "UpdatedPassword!2"
        v.update(c)
        assert len(c.password_history) == 1
        assert c.password_history[0]["password"] == "InitialPassword!1"

        # 软删除 → 回收站 → 恢复
        v.soft_delete(c.id)
        assert len(v.active_credentials()) == 0
        assert len(v.trash_credentials()) == 1
        assert v.stats()["trash_count"] == 1
        v.restore(c.id)
        assert len(v.active_credentials()) == 1

        # 重开验证所有字段完整持久化且磁盘无明文
        raw = open(p, "rb").read()
        assert b"ghp_secret_value_123" not in raw
        assert b"JBSWY3DPEHPK3PXP" not in raw
        assert b"13800138000" not in raw

        v2 = Vault.unlock(p, "StrongPass!99")
        got = v2.get(c.id)
        assert got is not None
        assert got.totp_secret == "JBSWY3DPEHPK3PXP"
        assert got.recovery_phone == "13800138000"
        assert got.recovery_email == "backup@example.com"
        assert len(got.recovery_items) == 3
        assert got.api_keys[0]["value"] == "ghp_secret_value_123"
        assert got.favorite is True
        assert got.matches("13800138000")
        assert got.matches("GitHub PAT")


def test_v1_upgrade_to_v2_and_new_recovery_key():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "legacy.pmvault")
        old_rk = _make_legacy_v1_vault(
            p,
            "OldPass123!",
            [{"id": "e1", "name": "旧银行", "username": "u1", "password": "p1", "tags": ["金融"]}],
        )
        hdr = Vault.peek_header(p)
        assert hdr.version == 1

        # 用原密码解开旧库，检测到 migrated_from_v1
        v = Vault.unlock(p, "OldPass123!")
        assert v.migrated_from_v1 is True
        assert len(v.active_credentials()) == 1
        assert v.active_credentials()[0].name == "旧银行"

        # 完成单向升级到 v2 + 新一次性恢复码
        new_rk = v.complete_v1_upgrade("OldPass123!")
        assert new_rk != old_rk
        assert os.path.exists(p + ".v1.bak")
        assert v.migrated_from_v1 is False
        assert Vault.peek_header(p).version == FORMAT_VERSION

        # 旧恢复码彻底失效，新恢复码有效
        with pytest.raises(WrongPasswordError):
            Vault.unlock_with_recovery_key(p, old_rk)
        v_rec = Vault.unlock_with_recovery_key(p, new_rk)
        assert v_rec.must_reset_master_password is True


def test_onetime_recovery_forces_password_reset_and_invalidates_old():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "v.pmvault")
        v = Vault.create(p, "MasterPass1!")
        rk1 = v.last_recovery_key
        assert rk1
        v.add(Credential(name="邮箱", username="a@b.com", password="pw1"))

        # 用一次性恢复码登入
        v2 = Vault.unlock_with_recovery_key(p, rk1)
        assert v2.must_reset_master_password is True
        # 未重设主密码前禁止普通修改
        with pytest.raises(VaultResetRequiredError):
            v2.add(Credential(name="禁止", password="x"))

        # 强制改密并生成新一次性恢复码
        rk2 = v2.complete_recovery_reset("NewMasterPass2!")
        assert rk2 != rk1
        assert v2.must_reset_master_password is False

        # 旧主密码与旧恢复码均失效
        with pytest.raises(WrongPasswordError):
            Vault.unlock(p, "MasterPass1!")
        with pytest.raises(WrongPasswordError):
            Vault.unlock_with_recovery_key(p, rk1)

        # 新主密码与新恢复码均有效
        v3 = Vault.unlock(p, "NewMasterPass2!")
        assert len(v3.active_credentials()) == 1
        v4 = Vault.unlock_with_recovery_key(p, rk2)
        assert len(v4.active_credentials()) == 1


def test_keyfile_protection_and_recovery_bypass():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "v.pmvault")
        kf_path = os.path.join(td, "device.pmkey")
        kid, kf_bytes = crypto.new_keyfile()
        crypto.save_keyfile(kf_path, kf_bytes)

        # 二维码往返
        qr_str = crypto.encode_keyfile_qr(kf_bytes)
        dec_id, dec_bytes = crypto.decode_keyfile_qr(qr_str)
        assert dec_id == kid and dec_bytes == kf_bytes

        v = Vault.create(p, "short6", keyfile_bytes=kf_bytes)
        rk = v.last_recovery_key
        assert v.header.keyfile_required is True
        assert v.header.keyfile_id == kid

        # 缺密钥文件报错
        with pytest.raises(KeyfileRequiredError):
            Vault.unlock(p, "short6")

        # 错密钥文件报错
        _, wrong_kf = crypto.new_keyfile()
        with pytest.raises(KeyfileMismatchError):
            Vault.unlock(p, "short6", keyfile_bytes=wrong_kf)

        # 正确密码 + 正确密钥文件解锁
        loaded_id, loaded_kf = crypto.load_keyfile(kf_path)
        assert loaded_id == kid
        v_ok = Vault.unlock(p, "short6", keyfile_bytes=loaded_kf)
        assert v_ok.header.keyfile_id == kid

        # 极端兜底：既忘密码又丢密钥文件，一次性恢复码依然可解开并重设
        v_rec = Vault.unlock_with_recovery_key(p, rk)
        new_rk = v_rec.complete_recovery_reset("AnotherPass8!", keyfile_bytes=None)
        assert new_rk != rk
        v_plain = Vault.unlock(p, "AnotherPass8!")
        assert v_plain.header.keyfile_required is False


def test_totp_rfc6238_and_otpauth():
    # RFC 6238 官方测试向量: secret="12345678901234567890" (Base32: GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ), t=59 -> 94287082 (8位) / 287082 (6位)
    code6, rem, period = totp.generate_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", for_time=59)
    assert code6 == "287082"
    assert rem == 1 and period == 30

    uri = "otpauth://totp/GitHub:phis123?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=GitHub&digits=8&period=30"
    code8, _, _ = totp.generate_totp(uri, for_time=59)
    assert code8 == "94287082"
    assert totp.format_code("287082") == "287 082"


def test_dual_channel_sync_and_legacy_pm1_import():
    creds = [
        Credential(
            name="Google",
            username="u@gmail.com",
            password="P@ssword123",
            totp_secret="JBSWY3DPEHPK3PXP",
            api_keys=[{"label": "API", "value": "sk-123", "note": ""}],
        )
    ]
    _, kf_bytes = crypto.new_keyfile()
    rk = crypto.new_recovery_key()

    pkg = sync.pack_dual_channel(
        creds,
        revision=7,
        device="pc",
        vault_name="主库",
        master_password="MyLoginPass!",
        keyfile_bytes=kf_bytes,
        recovery_key=rk,
    )
    lines = pkg["file_text"].splitlines()
    assert sync.detect_format(lines) == "PM2"

    # 缺二维码报错
    with pytest.raises(sync.SyncError):
        sync.unpack_dual_channel(lines, "")

    # 缺文件报错
    with pytest.raises(sync.SyncError):
        sync.unpack_dual_channel([], pkg["qr_payload"])

    # 完整双通道解包
    out = sync.unpack_dual_channel(lines, pkg["qr_payload"])
    assert len(out["credentials"]) == 1
    assert out["credentials"][0].totp_secret == "JBSWY3DPEHPK3PXP"
    assert out["master_password"] == "MyLoginPass!"
    assert out["keyfile_bytes"] == kf_bytes
    assert out["recovery_key"] == rk
    assert out["vault_name"] == "主库"

    # 二维码和另一份文件不配对报错
    pkg2 = sync.pack_dual_channel(creds, revision=8, device="pc")
    with pytest.raises(sync.SyncError):
        sync.unpack_dual_channel(lines, pkg2["qr_payload"])

    # 旧版 PM1 单向兼容导入
    pm1_lines = _make_legacy_pm1_lines(creds, "legacy-sync-pass")
    assert sync.detect_format(pm1_lines) == "PM1"
    old_out = sync.unpack(pm1_lines, "legacy-sync-pass")
    assert old_out["legacy_pm1"] is True
    assert old_out["credentials"][0].name == "Google"


def test_multi_vault_registry_and_legacy_discovery():
    with tempfile.TemporaryDirectory() as td:
        data_dir = os.path.join(td, "data")
        key_dir = os.path.join(td, "isolated_keys")
        os.makedirs(data_dir, exist_ok=True)
        # 模拟旧版遗留 data/vault.pmvault
        legacy_p = os.path.join(data_dir, "vault.pmvault")
        _make_legacy_v1_vault(legacy_p, "OldPass123!", [{"id": "1", "name": "老条目", "password": "abc"}])

        reg = VaultRegistry(data_dir, keyfile_dir=key_dir)
        vaults = reg.list_vaults()
        assert len(vaults) == 1
        assert vaults[0]["is_v1"] is True
        assert os.path.normcase(vaults[0]["path"]) == os.path.normcase(legacy_p)

        # 新建第二个独立库（带隔离本机密钥）
        rec2, v2, kf_path = reg.create_vault("工作库", "WorkPass8!", use_keyfile=True)
        assert kf_path is not None
        assert os.path.dirname(kf_path) == os.path.abspath(key_dir)
        assert len(reg.list_vaults()) == 2
        assert reg.list_vaults()[1]["name"] == "工作库"
        assert reg.list_vaults()[1]["keyfile_required"] is True

        # 验证 stats(old_days) 不再硬编码 180
        c_old = Credential(name="旧密码", password="StrongEnough!99", password_updated_at="2025-01-01T00:00:00+08:00")
        v2.add(c_old)
        assert v2.stats(old_days=10)["old"] == 1
        assert v2.stats(old_days=9999)["old"] == 0

        # 验证二维码 SVG 生成（若已安装 segno）
        svg_b64 = qr.make_qr_svg_b64("PMKEY1:TEST:ABC")
        assert svg_b64 is not None and len(svg_b64) > 50

        # 统一 settings 往返
        sp = os.path.join(data_dir, "settings.json")
        s = settings_mod.load(sp)
        s["old_password_days"] = 90
        settings_mod.save(sp, s)
        assert settings_mod.load(sp)["old_password_days"] == 90
