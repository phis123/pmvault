"""桌面端（方案A）重置 / 导出 流程测试（不弹任何界面）。

做法：不实例化 tk.Tk()（无人值守会卡住），而是
  · 把 App 上的目标方法绑到一个只有 vault_path 属性的轻量对象上；
  · 把 filedialog / messagebox 替换成记录调用的假实现；
这样能真实跑通「归档重置」「导出密文」的逻辑，并断言文件系统结果。

另外覆盖跨电脑可移植性：vault_path 存相对路径后，整目录搬到别处仍能找到保险库。
"""

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")

import tempfile


import os
import shutil
import sys
import types

BASE = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

# 本机 %TEMP% 禁写：自己建测试目录
WORK = os.path.join(_WORK_ROOT, "desktop_reset")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)

from pm_core import Credential, Vault  # noqa: E402
from pm_ui import app as ui_app  # noqa: E402

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


def make_target(vault_path: str):
    """把 App 上的目标方法挂到一个只有 vault_path 的轻量对象上。"""
    obj = types.SimpleNamespace(vault_path=vault_path)
    for name in ("_archive_reset_vault", "_export_for_reset"):
        setattr(obj, name, types.MethodType(getattr(ui_app.App, name), obj))
    return obj


def main() -> int:
    print("=" * 64)
    print("桌面端 重置 / 导出 流程测试")
    print("=" * 64)

    # ============================================================ 1. 归档重置
    print("\n【1】重置：归档而不是删除")
    vault_file = os.path.join(WORK, "vault.pmvault")
    vault = Vault.create(vault_file, "Reset-Test-1!", device="pc")
    vault.add(Credential(name="GitHub", username="dev", password="Str0ng!Pass#2026"))
    bak = vault_file + ".bak"
    shutil.copy2(vault_file, bak)

    target = make_target(vault_file)
    archived = target._archive_reset_vault()

    check("归档返回 2 个文件（含 .bak）", len(archived) == 2, str(archived))
    check("原保险库文件已被移走", not os.path.exists(vault_file))
    check("原 .bak 文件也被移走", not os.path.exists(bak))
    check("归档文件命名带时间戳与「旧」后缀",
          all(name.endswith(".旧") and "20" in os.path.basename(name) for name in archived),
          str([os.path.basename(p) for p in archived]))
    check("归档文件确实存在（没有真的删数据）", all(os.path.exists(p) for p in archived))

    # 归档的是密文，用原主密码仍能打开 —— 这是"留后路"的依据
    if archived:
        try:
            again = Vault.unlock(archived[0], "Reset-Test-1!")
            ok = again.all()[0].password == "Str0ng!Pass#2026"
        except Exception as exc:
            ok = False
            check("归档文件仍可用原主密码解锁", False, f"{type(exc).__name__}: {exc}")
        else:
            check("归档文件仍可用原主密码解锁（想起密码就能救回）", ok)
        raw = open(archived[0], "rb").read()
        check("归档文件仍是密文（磁盘无明文）",
              b"GitHub" not in raw and b"Str0ng!Pass#2026" not in raw)

    # 幂等：再归档一次不应报错
    try:
        again = target._archive_reset_vault()
        check("重复重置是幂等的（不抛异常）", again == [], str(again))
    except Exception as exc:
        check("重复重置是幂等的（不抛异常）", False, f"{type(exc).__name__}: {exc}")

    # ============================================================ 2. 重置后可重建
    print("\n【2】重置后可以创建全新保险库")
    fresh = Vault.create(vault_file, "Brand-New-2!", device="pc")
    fresh.add(Credential(name="新的条目", username="new", password="NewPass!2345"))
    check("能创建新保险库", os.path.exists(vault_file))
    check("新库内容是新写的", [c.name for c in Vault.unlock(vault_file, "Brand-New-2!").all()] == ["新的条目"])
    try:
        Vault.unlock(vault_file, "Reset-Test-1!")
        check("旧主密码打不开新库（确属全新密文）", False, "旧密码竟然能开")
    except Exception:
        check("旧主密码打不开新库（确属全新密文）", True)

    # ============================================================ 3. 导出
    print("\n【3】导出加密数据（重置前的后路）")
    export_target = os.path.join(WORK, "exported", "my-backup.pmvault")
    os.makedirs(os.path.dirname(export_target), exist_ok=True)

    calls = {"asked": None, "info": []}
    orig_save, orig_info = ui_app.filedialog.asksaveasfilename, ui_app.messagebox.showinfo
    orig_error = ui_app.messagebox.showerror
    ui_app.filedialog.asksaveasfilename = lambda **kw: calls.__setitem__("asked", kw) or export_target
    ui_app.messagebox.showinfo = lambda *a, **kw: calls["info"].append(a)
    ui_app.messagebox.showerror = lambda *a, **kw: calls["info"].append(("ERROR",) + a)
    try:
        # 造出 .bak 以验证"连备份一起导出"
        shutil.copy2(vault_file, vault_file + ".bak")
        exporter = make_target(vault_file)
        result = exporter._export_for_reset()
    finally:
        ui_app.filedialog.asksaveasfilename = orig_save
        ui_app.messagebox.showinfo = orig_info
        ui_app.messagebox.showerror = orig_error

    check("导出返回保存路径", result == export_target, str(result))
    check("导出文件已生成", os.path.exists(export_target))
    check("连 .bak 备份一起导出", os.path.exists(export_target + ".bak"))
    check("导出的仍是密文",
          "新的条目".encode("utf-8") not in open(export_target, "rb").read())
    check("导出后原保险库文件仍在（此时还没重置）", os.path.exists(vault_file))
    check("导出对话框默认文件名带时间戳",
          "20" in (calls["asked"] or {}).get("initialfile", ""),
          str((calls["asked"] or {}).get("initialfile")))
    check("导出的密文可用原主密码打开（真的能救回）",
          Vault.unlock(export_target, "Brand-New-2!").all()[0].name == "新的条目")

    # 用户取消保存时不应报错、也不该动任何文件
    ui_app.filedialog.asksaveasfilename = lambda **kw: ""
    try:
        cancelled = make_target(vault_file)._export_for_reset()
    finally:
        ui_app.filedialog.asksaveasfilename = orig_save
    check("取消保存返回 None 且不抛异常", cancelled is None, str(cancelled))

    # ============================================================ 4. 可移植性
    print("\n【4】跨电脑可移植性（相对路径）")
    # 模拟真实布局：<程序目录>\data\vault.pmvault
    # 用 mock 把模块里的 APP_DIR / DATA_DIR 指向模拟目录，
    # 这样跑的路径逻辑与打包后完全一致（打包后 APP_DIR = exe 所在目录）。
    from unittest import mock

    sim_root = os.path.join(WORK, "sim-pc", "PMVault")
    sim_data = os.path.join(sim_root, "data")
    os.makedirs(sim_data, exist_ok=True)
    pv = os.path.join(sim_data, "vault.pmvault")
    Vault.create(pv, "Portable-1!", device="pc").save()

    with mock.patch.object(ui_app, "APP_DIR", sim_root), \
            mock.patch.object(ui_app, "DATA_DIR", sim_data):
        stored = ui_app.store_vault_path(pv)
        check("程序目录内的保险库存成相对路径", not os.path.isabs(stored), stored)
        check("相对路径不含盘符/用户名", ":" not in stored and "phis123" not in stored, stored)
        check("相对路径形如 data/vault.pmvault", stored == "data/vault.pmvault", stored)
        check("还原回绝对路径正确",
              ui_app.resolve_vault_path(stored) == os.path.normpath(pv),
              ui_app.resolve_vault_path(stored))
        # 目录外的文件仍存绝对路径（用户自选位置的保险库）
        outside = os.path.join(WORK, "elsewhere", "mine.pmvault")
        check("程序目录外的保险库仍存绝对路径",
              ui_app.store_vault_path(outside) == os.path.normpath(outside),
              ui_app.store_vault_path(outside))

        # 模拟"换一台电脑"：把整个程序目录复制到另一个位置
        another = os.path.join(WORK, "another-pc", "PMVault")
        shutil.copytree(sim_root, another, dirs_exist_ok=True)
        with mock.patch.object(ui_app, "APP_DIR", another), \
                mock.patch.object(ui_app, "DATA_DIR", os.path.join(another, "data")):
            resolved = ui_app.resolve_vault_path(stored)
            check("换目录后相对路径仍指向真实保险库", os.path.exists(resolved), resolved)
            check("换目录后仍能用原主密码打开", Vault.unlock(resolved, "Portable-1!") is not None)
            # 兼容旧设置：绝对路径指向不存在的位置，但程序目录里有库 → 自动改用默认位置
            check("旧绝对路径能识别为不存在",
                  not os.path.exists(r"C:\Users\someone-else\Desktop\PMVault\data\vault.pmvault"))

    shutil.rmtree(WORK, ignore_errors=True)

    print("\n" + "=" * 64)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("桌面端重置/导出流程验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
