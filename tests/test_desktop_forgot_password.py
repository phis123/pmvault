"""桌面端解锁页「忘记密码」全流程测试（真实 Tk 界面，窗口保持隐藏）。

不派人点界面：把 messagebox 换成脚本化应答，直接跑 App 上的真实流程方法，
断言最终的文件系统状态与界面状态。

注意：本文件会创建 Tk 窗口（withdraw 隐藏），但不显示、不需要人操作。
因为工作区禁止运行 exe，这里跑的是 python 源码，不受影响。
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
from unittest import mock

BASE = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

WORK = os.path.join(_WORK_ROOT, "desktop_flow")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)

from pm_core import Credential, Vault  # noqa: E402
from pm_ui import app as ui_app  # noqa: E402

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


def widgets(root):
    """递归收集所有子控件。"""
    found = []
    for child in root.winfo_children():
        found.append(child)
        found.extend(widgets(child))
    return found


def texts(root):
    out = []
    for w in widgets(root):
        try:
            out.append(str(w.cget("text")))
        except Exception:
            pass
    return out


def main() -> int:
    print("=" * 64)
    print("桌面端「忘记密码」全流程测试（真实 Tk，窗口隐藏）")
    print("=" * 64)

    data_dir = os.path.join(WORK, "data")
    os.makedirs(data_dir, exist_ok=True)
    vault_file = os.path.join(data_dir, "vault.pmvault")

    # 造一个真实保险库（模拟用户已存了密码但忘了主密码的情形）
    v = Vault.create(vault_file, "Forgotten-1!", device="pc")
    v.add(Credential(name="GitHub", username="dev", password="Str0ng!Pass#2026"))
    shutil.copy2(vault_file, vault_file + ".bak")

    # 让程序认这个保险库（settings 里的相对路径是相对程序目录的，
    # 这里直接把 vault_path 写成测试用的绝对路径）
    import json
    with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"vault_path": vault_file}, fh, ensure_ascii=False)

    dialogs_seen = []

    def fake_yesno(title, message, **kw):
        dialogs_seen.append(("yesno", message))
        # 第 1 步问"还能找到恢复密钥吗" → 回答「否」= 继续重置
        return False

    def fake_yesnocancel(title, message, **kw):
        dialogs_seen.append(("yesnocancel", message))
        return False        # 「否」= 不导出，直接重置

    def fake_showinfo(*a, **kw):
        dialogs_seen.append(("info", a[1] if len(a) > 1 else ""))

    def fake_ask_text(self, title, prompt, expected):
        dialogs_seen.append(("ask_text", prompt))
        return True         # 模拟用户正确输入了「重置」

    app = ui_app.App(data_dir=data_dir)
    app.withdraw()

    with mock.patch.object(ui_app.messagebox, "askyesno", fake_yesno), \
            mock.patch.object(ui_app.messagebox, "askyesnocancel", fake_yesnocancel), \
            mock.patch.object(ui_app.messagebox, "showinfo", fake_showinfo), \
            mock.patch.object(ui_app.messagebox, "showerror", fake_showinfo), \
            mock.patch.object(ui_app.App, "_ask_text", fake_ask_text):
        try:
            # ---------------------------------------------------- 解锁页入口
            app.show_unlock()
            labels = texts(app.container)
            check("解锁页出现「忘记密码？」入口", "忘记密码？" in labels, str(labels[:6]))
            check("保险库存在时显示「解锁」按钮", "解锁" in labels, str(labels))
            check("存在「用恢复密钥解锁」按钮", "用恢复密钥解锁" in labels)

            # ---------------------------------------------------- 跑完整流程
            app._forgot_password()
        finally:
            try:
                app.destroy()
            except Exception:
                pass

    # ---------------------------------------------------- 断言流程与结果
    kinds = [k for k, _ in dialogs_seen]
    check("流程依次弹出：说明 → 导出询问 → 强确认 → 完成提示",
          kinds == ["yesno", "yesnocancel", "ask_text", "info"], str(kinds))
    first_msg = next((m for k, m in dialogs_seen if k == "yesno"), "")
    check("说明里明确写出「密码学上无法恢复」", "无法恢复" in first_msg)
    check("说明里给出恢复密钥这条出路", "恢复密钥" in first_msg and "ABCD-EFGH" in first_msg)
    second_msg = next((m for k, m in dialogs_seen if k == "yesnocancel"), "")
    check("导出询问里说明导出的是密文", "密文" in second_msg)
    third_msg = next((m for k, m in dialogs_seen if k == "ask_text"), "")
    check("强确认要求输入「重置」", "重置" in third_msg)

    check("原保险库文件已不在原位置", not os.path.exists(vault_file))
    check("原 .bak 也已被归档", not os.path.exists(vault_file + ".bak"))
    archived = [n for n in os.listdir(data_dir) if n.endswith(".旧")]
    check("归档文件已生成（数据没被真删）", len(archived) == 2, str(archived))
    if archived:
        try:
            still = Vault.unlock(os.path.join(data_dir, archived[0]), "Forgotten-1!")
            ok = still.all()[0].name == "GitHub"
        except Exception as exc:
            ok, archived = False, [f"{type(exc).__name__}: {exc}"]
        check("归档文件仍可用原主密码打开（后路有效）", ok, str(archived))

    # ---------------------------------------------------- 重置后可重新创建
    app2 = ui_app.App(data_dir=data_dir)
    app2.withdraw()
    try:
        labels = texts(app2.container)
        check("重置后解锁页变为创建模式", "创建并解锁" in labels, str(labels))
        check("重置后不再显示「忘记密码？」", "忘记密码？" not in labels)
        check("重置后不再显示「用恢复密钥解锁」", "用恢复密钥解锁" not in labels)
        # 真的能新建
        app2.vault = Vault.create(app2.vault_path, "BrandNew-2!", device="pc")
        check("能创建全新保险库", os.path.exists(app2.vault_path))
        app2.show_main()
        check("主面板可构建（进入正常使用状态）", app2.vault is not None)
    finally:
        try:
            app2.destroy()
        except Exception:
            pass

    shutil.rmtree(WORK, ignore_errors=True)

    print("\n" + "=" * 64)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("桌面端「忘记密码」全流程验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
