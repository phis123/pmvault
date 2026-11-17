"""桌面端界面「值真的显示出来了吗」回归测试。

覆盖两类曾经真实发生、且原来测试完全没覆盖的 bug：

1. **`tk.StringVar` 被垃圾回收** → 标签的 textvariable 指向已注销的变量 → 该行显示空白
   （现象：详情页只有「用户名」「备注」标题，值不见了）
2. **回调在控件构造期间触发** → 回调引用了后面才赋值的名字 →
   `NameError: cannot access free variable 'error'`
   （现象：密码生成器每次打开都在后台抛异常、滑块不联动）

做法：真实创建窗口（withdraw 隐藏），直接读取每个绑定变量控件的值。
不依赖人工点击、不依赖截图。
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
import tkinter as tk

BASE = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

WORK = os.path.join(_WORK_ROOT, "tkfields")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)

from pm_core import Credential, Vault  # noqa: E402

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


def bound_widgets(widget, found=None):
    """收集所有绑定了 variable/textvariable 的控件及其当前值。"""
    if found is None:
        found = []
    for child in widget.winfo_children():
        for opt in ("variable", "textvariable"):
            try:
                name = child.cget(opt)
            except Exception:
                continue
            if not name:
                continue
            try:
                value = child.getvar(name)
                err = ""
            except Exception as exc:
                value, err = None, f"{type(exc).__name__}"
            found.append((child.winfo_class(), opt, value, err))
        bound_widgets(child, found)
    return found


def main() -> int:
    print("=" * 68)
    print("桌面端界面值显示回归测试")
    print("=" * 68)

    data_dir = os.path.join(WORK, "data")
    os.makedirs(data_dir, exist_ok=True)
    vault_file = os.path.join(data_dir, "vault.pmvault")
    import json
    with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"vault_path": vault_file}, fh, ensure_ascii=False)

    from pm_ui import app as ui_app
    from pm_ui import dialogs, widgets

    app = ui_app.App(data_dir=data_dir)
    app.withdraw()
    app.vault = Vault.create(vault_file, "Fields-Test-1!", device="pc")

    cred = Credential(name="google", username="snik3479@gmail.com", password="Abc12345")
    cred.notes = "Tailsale用的也是这个"
    cred.tags = ["dev", "mail"]
    cred.url = "https://google.com"
    app.vault.add(cred)
    app.vault.save()

    # ============================================================ 1. 详情页
    print("\n【1】详情页各字段的值必须真的显示出来（曾经全空白）")
    app.selected_id = cred.id
    app.show_main()
    app.refresh_list()
    app.update()
    app._render_detail(cred)
    app.update()

    items = bound_widgets(app.detail)
    empty = [i for i in items if i[3] or i[2] in ("", None)]
    check("详情页绑定的变量都有值（无空/无错）", not empty, f"异常项: {empty}")

    values = [str(v) for _c, _o, v, e in items if not e]
    for expect, label in ((cred.username, "用户名"), (cred.notes, "备注"),
                          ("dev, mail", "标签"), (cred.password, "密码")):
        check(f"详情页显示了{label}", expect in values, f"实际 values={values}")

    # ============================================================ 2. 空值字段
    print("\n【2】空字段应显示占位符而不是留白")
    c2 = Credential(name="no-notes", username="", password="x")
    app.vault.add(c2)
    app._render_detail(c2)
    app.update()
    items2 = bound_widgets(app.detail)
    vals2 = [str(v) for _c, _o, v, e in items2 if not e]
    check("空用户名显示为占位符", "—" in vals2, f"values={vals2}")

    # ============================================================ 3. 编辑对话框
    print("\n【3】条目编辑对话框各字段预填正确")
    dlg = dialogs.CredentialDialog(app, {}, cred)
    dlg.withdraw()
    app.update()
    for name, expect in (("name_var", cred.name), ("user_var", cred.username),
                         ("url_var", cred.url), ("tags_var", "dev, mail")):
        try:
            got = getattr(dlg, name).get()
            check(f"{name} = {got!r}", got == expect, f"期望 {expect!r}")
        except Exception as exc:
            check(f"{name} 可读", False, f"{type(exc).__name__}: {exc}")
    notes = dlg.notes.get("1.0", "end").strip()
    check("备注预填正确", notes == cred.notes, repr(notes))
    dlg.destroy()
    app.update()

    # ============================================================ 4. 密码生成器
    print("\n【4】密码生成器：打开不报错、滑块联动、预览会重新生成")
    report = {}

    def scan_and_exercise():
        wins = [w for w in app.winfo_children()
                if isinstance(w, tk.Toplevel) and w.winfo_exists()]
        if not wins:
            app.after(60, scan_and_exercise)
            return
        win = wins[0]
        win.withdraw()
        scale = None
        labels = []
        entries = []

        def find(w):
            nonlocal scale
            for c in w.winfo_children():
                cls = c.winfo_class()
                if cls == "TScale":
                    scale = c
                if cls == "TLabel":
                    try:
                        t = str(c.cget("text"))
                        if t.isdigit():
                            labels.append(c)
                    except Exception:
                        pass
                if cls == "TEntry":
                    entries.append(c)
                find(c)

        find(win)
        report["labels_before"] = [l.cget("text") for l in labels]
        report["preview_before"] = [e.get() for e in entries]
        if scale is not None:
            scale.set(32)
            app.update()
        report["labels_after"] = [l.cget("text") for l in labels]
        report["preview_after"] = [e.get() for e in entries]
        report["bound"] = bound_widgets(win)
        # 找到「使用此密码」按钮并点击，验证对话框把预览值返回出去
        # （否则是程序化关窗，返回值当然是 None，会误判）
        use_btn = None

        def find_btn(w):
            nonlocal use_btn
            for c in w.winfo_children():
                if c.winfo_class() == "TButton":
                    try:
                        if "使用" in str(c.cget("text")):
                            use_btn = c
                    except Exception:
                        pass
                find_btn(c)

        find_btn(win)
        if use_btn is not None:
            use_btn.invoke()
            app.update()
        if win.winfo_exists():
            win.destroy()

    app.after(80, scan_and_exercise)
    try:
        result = widgets.generator_dialog(app, "Start-1234")
    except Exception as exc:
        result = None
        check("生成器打开不抛异常", False, f"{type(exc).__name__}: {exc}")

    b, a = report.get("labels_before", []), report.get("labels_after", [])
    check("生成器里长度数字联动滑块", bool(a) and a != b and "32" in a, f"{b} → {a}")
    pb, pa = report.get("preview_before", []), report.get("preview_after", [])
    check("生成器里预览会重新生成", bool(pb) and pb != pa, f"{pb} → {pa}")
    bound = report.get("bound", [])
    bad = [i for i in bound if i[3] or i[2] in ("", None)]
    check("生成器里绑定的变量都有值（无 NameError 残留）", not bad, f"异常项: {bad}")
    check("点「使用此密码」返回了密码", bool(result) and len(result) >= 6,
          f"返回 {result!r}")

    # ============================================================ 5. 清理
    app.destroy()
    shutil.rmtree(WORK, ignore_errors=True)

    print("\n" + "=" * 68)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("界面值显示回归测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
