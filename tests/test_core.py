"""核心层测试：加密往返、错误口令、篡改检测、同步分片、生成器。"""

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")


import json
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pm_core import Vault, Credential, crypto, sync, generator  # noqa: E402


@pytest.fixture()
def tmpdir():
    d = tempfile.mkdtemp(prefix="pmtest_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def make_vault(tmpdir, password="Correct-Horse-9!"):
    path = os.path.join(tmpdir, "vault.pmvault")
    return Vault.create(path, password), path


# ---------------------------------------------------------------- KDF / AEAD

def test_kdf_deterministic_and_salted():
    salt = crypto.new_salt()
    k1 = crypto.derive_kek("pw", salt, {"algo": "scrypt", "n": 1 << 12, "r": 8, "p": 1})
    k2 = crypto.derive_kek("pw", salt, {"algo": "scrypt", "n": 1 << 12, "r": 8, "p": 1})
    k3 = crypto.derive_kek("pw2", salt, {"algo": "scrypt", "n": 1 << 12, "r": 8, "p": 1})
    assert k1 == k2 and k1 != k3 and len(k1) == 32


def test_aead_roundtrip_and_tamper():
    key = os.urandom(32)
    blob = crypto.encrypt(key, b"secret", aad=b"ctx")
    assert crypto.decrypt(key, blob, aad=b"ctx") == b"secret"
    with pytest.raises(crypto.TamperError):
        crypto.decrypt(os.urandom(32), blob, aad=b"ctx")
    bad = dict(blob)
    raw = bytearray(crypto.b64d(bad["data"]))
    raw[0] ^= 0x01
    bad["data"] = crypto.b64e(bytes(raw))
    with pytest.raises(crypto.TamperError):
        crypto.decrypt(key, bad, aad=b"ctx")
    with pytest.raises(crypto.TamperError):
        crypto.decrypt(key, blob, aad=b"other-context")


def test_auth_hash_separate_from_enc_key():
    key = os.urandom(32)
    assert crypto.auth_hash(key) != crypto.subkey(key, "vault-enc")


# ---------------------------------------------------------------- 保险库

def test_create_add_reopen(tmpdir):
    vault, path = make_vault(tmpdir)
    vault.add(Credential(name="GitHub", username="alice", password="pw1", tags=["dev"]))
    vault.add(Credential(name="邮箱", username="alice@example.com", password="pw2"))

    reopened = Vault.unlock(path, "Correct-Horse-9!")
    assert len(reopened.all()) == 2
    gh = [c for c in reopened.all() if c.name == "GitHub"][0]
    assert gh.username == "alice" and gh.password == "pw1" and gh.tags == ["dev"]


def test_no_plaintext_on_disk(tmpdir):
    vault, path = make_vault(tmpdir)
    vault.add(Credential(name="GitHub", username="alice-secret-user", password="SuperSecret123", url="https://github.com"))
    raw = open(path, "rb").read()
    for needle in (b"GitHub", b"alice-secret-user", b"SuperSecret123", b"github.com"):
        assert needle not in raw, f"磁盘上出现明文：{needle!r}"


def test_wrong_password_rejected(tmpdir):
    _vault, path = make_vault(tmpdir)
    with pytest.raises(crypto.WrongPasswordError):
        Vault.unlock(path, "wrong-password")


def test_tampered_file_detected(tmpdir):
    vault, path = make_vault(tmpdir)
    vault.add(Credential(name="X", password="y"))
    doc = crypto.json_loads(open(path, "rb").read())
    blob = bytearray(crypto.b64d(doc["payload"]["data"]))
    blob[5] ^= 0xFF
    doc["payload"]["data"] = crypto.b64e(bytes(blob))
    open(path, "w", encoding="utf-8").write(json.dumps(doc, ensure_ascii=False))
    with pytest.raises(crypto.TamperError):
        Vault.unlock(path, "Correct-Horse-9!")


def test_update_records_password_change(tmpdir):
    vault, path = make_vault(tmpdir)
    cred = vault.add(Credential(name="A", password="old"))
    original = cred.password_updated_at
    cred.password = "new"
    vault.update(cred)
    assert vault.get(cred.id).password_updated_at >= original
    assert Vault.unlock(path, "Correct-Horse-9!").get(cred.id).password == "new"


def test_delete(tmpdir):
    vault, _path = make_vault(tmpdir)
    cred = vault.add(Credential(name="A"))
    vault.delete(cred.id)
    assert vault.all() == []
    with pytest.raises(Exception):
        vault.delete("not-exist")


def test_validate_rejects_empty_name(tmpdir):
    vault, _path = make_vault(tmpdir)
    with pytest.raises(ValueError):
        vault.add(Credential(name="   "))


def test_change_master_password(tmpdir):
    vault, path = make_vault(tmpdir)
    vault.add(Credential(name="A", password="p"))
    vault.change_master_password("Correct-Horse-9!", "New-Master-77!")
    with pytest.raises(crypto.WrongPasswordError):
        Vault.unlock(path, "Correct-Horse-9!")
    reopened = Vault.unlock(path, "New-Master-77!")
    assert reopened.all()[0].password == "p"


def test_recovery_key_roundtrip(tmpdir):
    path = os.path.join(tmpdir, "vault.pmvault")
    vault = Vault.create(path, "Master-1!")
    assert vault.last_recovery_key and len(vault.last_recovery_key.split("-")) == 6
    vault.add(Credential(name="A", password="p"))
    rk = vault.rotate_recovery_key()

    reopened = Vault.unlock_with_recovery_key(path, rk.lower().replace("-", " "))
    assert reopened.all()[0].password == "p"
    with pytest.raises(crypto.WrongPasswordError):
        Vault.unlock_with_recovery_key(path, crypto.new_recovery_key())


def test_backup_file_created(tmpdir):
    vault, path = make_vault(tmpdir)
    vault.add(Credential(name="A"))
    vault.add(Credential(name="B"))
    assert os.path.exists(path + ".bak")


def test_search(tmpdir):
    vault, _path = make_vault(tmpdir)
    vault.add(Credential(name="GitHub", username="alice", tags=["dev"]))
    vault.add(Credential(name="银行", username="bob", tags=["金融"]))
    assert len(vault.search("github")) == 1
    assert len(vault.search("dev")) == 1
    assert len(vault.search("alice dev")) == 1
    assert len(vault.search("")) == 2


# ---------------------------------------------------------------- 同步

def _creds(n):
    return [Credential(name=f"账号{i}", username=f"user{i}", password=f"pw-{i}") for i in range(n)]


@pytest.mark.parametrize("count", [1, 10, 50, 200])
def test_sync_roundtrip(count):
    lines = sync.pack(_creds(count), "sync-pass-1", revision=3, device="pc-a")
    assert all(line.startswith("PM1-3-") for line in lines)
    data = sync.unpack(lines, "sync-pass-1")
    assert data["count"] == count and data["v"] == 3 and len(data["credentials"]) == count
    assert data["credentials"][0].name == "账号0"


def test_sync_missing_chunk_reports_index():
    lines = sync.pack(_creds(30), "pw", revision=1, device="pc")
    removed = lines[:2] + lines[3:]
    with pytest.raises(sync.MissingChunksError) as exc:
        sync.unpack(removed, "pw")
    assert exc.value.missing == [3] and exc.value.total == len(lines)


def test_sync_wrong_passphrase():
    lines = sync.pack(_creds(5), "right", revision=1, device="pc")
    with pytest.raises(sync.SyncError):
        sync.unpack(lines, "wrong")


def test_sync_no_plaintext_in_string():
    lines = sync.pack([Credential(name="GitHub", username="alice-secret", password="SuperSecret123")], "pw", revision=1, device="pc")
    joined = "".join(lines)
    for needle in ("GitHub", "alice-secret", "SuperSecret123"):
        assert needle not in joined


def test_sync_conflict_resolution():
    assert sync.resolve_conflict(1, 2, "a", "b") == "pull"
    assert sync.resolve_conflict(3, 2, "a", "b") == "push"
    assert sync.resolve_conflict(2, 2, "a", "b") == "conflict"
    assert sync.resolve_conflict(2, 2, "a", "a") == "none"


# ---------------------------------------------------------------- 生成器

def test_generator_respects_options():
    pw = generator.generate_password({"length": 24, "symbols": False, "exclude_confusing": True})
    assert len(pw) == 24
    assert not any(ch in generator.CONFUSING for ch in pw)
    assert any(ch.islower() for ch in pw) and any(ch.isupper() for ch in pw) and any(ch.isdigit() for ch in pw)


def test_generator_uniqueness():
    pool = {generator.generate_password({"length": 16}) for _ in range(200)}
    assert len(pool) == 200


def test_generator_rejects_bad_options():
    with pytest.raises(ValueError):
        generator.generate_password({"length": 3})
    with pytest.raises(ValueError):
        generator.generate_password({"lower": False, "upper": False, "digits": False, "symbols": False})


def test_password_strength_ordering():
    assert crypto.password_strength("123456")["score"] < crypto.password_strength("Xq7#mZ2!vLp9@Wr4")["score"]
