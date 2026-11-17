

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")

import tempfile
r"""pm_core.sync「文件导出 / 导入」接口回归测试。

覆盖主对话刚加的接口（out\pm_core\sync.py）：
  FILE_SUFFIX / header_lines / export_text / save_to_file / load_lines /
  unpack_file / default_filename / looks_like_sync_file，
以及 unpack 新增的「跳过空行与 `#` 开头行」（向后兼容：纯字符串仍能解）。

不依赖 UI、不联网；临时目录用 work\_testtmp\test_sync_file（不用 tempfile.mkdtemp）。
"""

import os
import shutil
import sys

BASE = _APP_DIR
OUT = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

WORK = os.path.join(_WORK_ROOT, "test_sync_file")
from pm_core import Credential, sync  # noqa: E402

PASS = "Sync-Pass-123!"
REV = 9
DEVICE = "dev-abcd1234"
FIELDS = ("name", "username", "password", "url", "tags", "notes")

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


def signature(cred):
    """取条目里我们关心的字段，用于逐字段比对。"""
    return tuple(tuple(getattr(cred, f)) if isinstance(getattr(cred, f), list)
                 else getattr(cred, f) for f in FIELDS)


def sample_credentials():
    """12 条、每条带一坨备注 —— 保证分片数 > 1，能真正检验分片行。"""
    creds = []
    for i in range(1, 13):
        creds.append(Credential(
            name=f"站点{i}",
            username=f"user{i}@example.com",
            password=f"Pw-{i}-Aa1!",
            url=f"https://site{i}.example.com",
            notes=f"第 {i} 条的备注，" + "备注内容" * 40,
            tags=[f"tag{i}", "dev"],
        ))
    return creds


def main() -> int:
    print("=" * 68)
    print("pm_core.sync 文件导出/导入回归测试")
    print("=" * 68)

    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)

    creds = sample_credentials()
    export_path = os.path.join(WORK, "export.pmvsync")

    # ============================================================ 1
    print("\n【1】export_text 的文本结构")
    text = sync.export_text(creds, PASS, REV, DEVICE)
    lines = text.splitlines()
    head_lines = [ln for ln in lines if ln.startswith("#")]
    chunk_lines = [ln for ln in lines if ln.startswith("PM1-")]
    check("导出文本含 # 说明行", len(head_lines) >= 3, f"说明行只有 {len(head_lines)} 行")
    packed = sync.pack(creds, PASS, REV, DEVICE)
    check("分片行数与 sync.pack() 相同",
          len(chunk_lines) == len(packed),
          f"文件 {len(chunk_lines)} 片 vs pack {len(packed)} 片")
    check("分片行格式为 PM1-<版本>-序号/总数-密文",
          bool(chunk_lines) and all(ln.startswith(f"PM1-{REV}-") for ln in chunk_lines),
          f"样例 {chunk_lines[:1]}")

    # ============================================================ 2
    print("\n【2】save_to_file 真的落盘")
    saved = sync.save_to_file(export_path, creds, PASS, REV, DEVICE)
    check("返回绝对路径", os.path.isabs(saved) and saved == os.path.abspath(saved), repr(saved))
    size = os.path.getsize(saved) if os.path.isfile(saved) else -1
    check("文件存在且非空", os.path.isfile(saved) and size > 0,
          f"exists={os.path.isfile(saved)} size={size}")

    # ============================================================ 3
    print("\n【3】正确口令往返解包")
    data = sync.unpack_file(export_path, PASS)
    got = data["credentials"]
    check("条目数与原始一致", len(got) == len(creds), f"{len(got)} vs {len(creds)}")
    diff = [(i, signature(a), signature(b))
            for i, (a, b) in enumerate(zip(got, creds)) if signature(a) != signature(b)]
    check("逐字段相等（name/username/password/url/tags/notes）", not diff, f"差异 {diff[:2]}")
    check("解包出的版本号与设备名正确",
          data.get("v") == REV and data.get("device") == DEVICE,
          f"v={data.get('v')!r} device={data.get('device')!r}")

    # ============================================================ 4
    print("\n【4】错误口令必须报错，不能静默返回空")
    wrong_err = None
    try:
        sync.unpack_file(export_path, "Wrong-Pass-999!")
    except Exception as exc:  # noqa: BLE001
        wrong_err = exc
    check("错误口令抛 SyncError（或子类）",
          isinstance(wrong_err, sync.SyncError),
          f"实际 {type(wrong_err).__name__}: {wrong_err}")

    # ============================================================ 5
    print("\n【5】篡改文件里一个分片字符必须报错")
    tampered = list(lines)
    pos = next(i for i, ln in enumerate(tampered) if ln.startswith("PM1-"))
    parts = tampered[pos].split("-", 3)          # PM1 / 版本 / 序号-总数 / 密文
    body = parts[3]
    parts[3] = ("A" if body[0] != "A" else "B") + body[1:]
    tampered[pos] = "-".join(parts)
    tamper_path = os.path.join(WORK, "tampered.pmvsync")
    with open(tamper_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(tampered) + "\n")
    tamper_err = None
    try:
        sync.unpack_file(tamper_path, PASS)
    except Exception as exc:  # noqa: BLE001
        tamper_err = exc
    check("改一个分片字符后解包报错（SyncError / SyncChecksumError）",
          isinstance(tamper_err, sync.SyncError),
          f"实际 {type(tamper_err).__name__}: {tamper_err}")

    # ============================================================ 6
    print("\n【6】load_lines 读回全部行")
    back = sync.load_lines(export_path)
    with open(export_path, "r", encoding="utf-8") as fh:
        raw_text = fh.read()
    raw_lines = raw_text.splitlines()
    check("load_lines 读回文件里的全部行（含说明行、空行）",
          back == raw_lines and len(back) == len(lines) and len(back) > len(chunk_lines),
          f"{len(back)} 行 vs 文件 {len(raw_lines)} 行 / 首次导出 {len(lines)} 行")
    check("读回内容里既有说明行也有分片行",
          any(ln.startswith("#") for ln in back) and any(ln.startswith("PM1-") for ln in back))

    # ============================================================ 7
    print("\n【7】unpack 跳过 # 行（向后兼容）")
    body_only = [ln for ln in back if ln and not ln.startswith("#")]
    data2 = sync.unpack(body_only, PASS)
    check("删掉说明行后解包结果与原来完全一致",
          [c.to_dict() for c in data2["credentials"]] == [c.to_dict() for c in got],
          f"{len(data2['credentials'])} 条")
    check("只给分片行（等价纯字符串）仍能解出全部条目",
          len(data2["credentials"]) == len(creds) and data2.get("v") == REV,
          f"{len(data2['credentials'])} 条 v={data2.get('v')!r}")

    # ============================================================ 8
    print("\n【8】looks_like_sync_file 粗判")
    check("只有分片行时判为同步包",
          bool(sync.looks_like_sync_file("\n".join(body_only))) is True)
    check("随便一段中文文本判为非同步包",
          bool(sync.looks_like_sync_file("这是一段随便写的中文文本，\n里面有换行和标点。")) is False)
    check("完整的导出文件也判为同步包", bool(sync.looks_like_sync_file(text)) is True)

    # ============================================================ 9
    print("\n【9】default_filename")
    fname = sync.default_filename(9, "dev-abcd1234")
    check("以 sync.FILE_SUFFIX 结尾", fname.endswith(sync.FILE_SUFFIX), fname)
    check("包含 v9", "v9" in fname, fname)
    fname0 = sync.default_filename(9)
    check("没给设备名时也不出现空片段",
          fname0.endswith(sync.FILE_SUFFIX) and "--" not in fname0 and "v9" in fname0, fname0)

    # ============================================================ 10
    print("\n【10】父目录不存在时自动创建")
    deep = os.path.join(WORK, "a", "b", "c", "deep.pmvsync")
    check("调用前父目录确实不存在", not os.path.isdir(os.path.dirname(deep)),
          os.path.dirname(deep))
    deep_saved = sync.save_to_file(deep, creds, PASS, REV, DEVICE)
    deep_size = os.path.getsize(deep_saved) if os.path.isfile(deep_saved) else -1
    check("父目录被自动创建且文件写入成功",
          os.path.isdir(os.path.dirname(deep_saved)) and deep_size > 0,
          f"{deep_saved} size={deep_size}")
    deep_data = sync.unpack_file(deep_saved, PASS)
    check("深层目录里的文件也能正常解包",
          len(deep_data["credentials"]) == len(creds), f"{len(deep_data['credentials'])} 条")

    # ============================================================ 11
    print("\n【11】不是同步包的文件要报错而不是崩溃")
    alien = os.path.join(WORK, "not-sync.txt")
    with open(alien, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("这不是同步文件。\n只是普通文本，带一个横线-和另一个-横线。\n")
    alien_err = None
    try:
        sync.unpack_file(alien, PASS)
    except Exception as exc:  # noqa: BLE001
        alien_err = exc
    check("读非同步包文本报 SyncError", isinstance(alien_err, sync.SyncError),
          f"实际 {type(alien_err).__name__}: {alien_err}")
    check("非同步包文本不会被认成同步包",
          bool(sync.looks_like_sync_file("这不是同步文件。")) is False)

    # ============================================================ 清理
    shutil.rmtree(WORK, ignore_errors=True)

    print("\n" + "=" * 68)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("sync 文件导出/导入测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
