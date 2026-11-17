"""桌面端弹窗定位（pm_ui.widgets.center_on_parent）回归测试。

背景：所有 tk.Toplevel 之前都没设 geometry，系统把弹窗丢到屏幕左上角（+0+0）；
主对话新增 `center_on_parent(win, parent, y_ratio=0.34)`，并在 dialogs/app 的弹窗
（含 SyncDialog）里于 wait_window 之前调用。

做法：真实创建 Tk 根窗口（withdraw 隐藏），真实创建 Toplevel 与 SyncDialog，
只读 geometry()，不进入 mainloop、不 wait_window，用完即 destroy。
本机没有可用 Tk（tk.TclError）时打印一行跳过并 return 0。
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
import re
import shutil
import sys

BASE = _APP_DIR
OUT = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

WORK = os.path.join(_WORK_ROOT, "test_desktop_dialogs")
import tkinter as tk  # noqa: E402

from pm_core import Credential, Vault  # noqa: E402

GEOM_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


def parse_geometry(win):
    """把 win.geometry() 解析成 (w, h, x, y)；解析不了返回 None。"""
    raw = win.geometry()
    m = GEOM_RE.match(raw.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))


class FakeParent:
    """只实现 center_on_parent 真正用到的查询，用于精确验证各分支。

    注意：**必须实现 `winfo_viewable()`**。实测（Windows / Tk 8.6）：
    从未映射过的窗口自报的是 `200x200@(34,57)` 而不是 `1x1`，
    所以"父窗口还没映射"只能靠 `winfo_viewable()` 判断，看尺寸会误判。
    """

    def __init__(self, x, y, w, h, viewable=True):
        self._x, self._y, self._w, self._h = x, y, w, h
        self._viewable = viewable

    def winfo_exists(self):
        return True

    def winfo_viewable(self):
        return self._viewable

    def winfo_rootx(self):
        return self._x

    def winfo_rooty(self):
        return self._y

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h


def main() -> int:
    print("=" * 68)
    print("桌面端弹窗定位（center_on_parent / SyncDialog）回归测试")
    print("=" * 68)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"  [skip] 本机没有可用的 Tk（{type(exc).__name__}: {exc}），跳过本测试")
        return 0

    created = []
    try:
        root.withdraw()
        check("能创建并隐藏 Tk 根窗口", bool(root.winfo_exists()))

        # 让父窗口有明确尺寸与位置（withdraw 状态下 geometry 依然生效）
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        rw = max(600, min(1000, sw - 200))
        rh = max(400, min(700, sh - 200))
        px, py = 100, 80
        root.geometry(f"{rw}x{rh}+{px}+{py}")
        root.update_idletasks()

        # ---------------------------------------------------- 临时保险库
        print("\n【1】临时保险库")
        shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK, exist_ok=True)
        vault_file = os.path.join(WORK, "dlg.pmvault")
        vault = Vault.create(vault_file, "Test-Pass-123!", device="dev-test")
        check("Vault.create 成功且文件落盘", os.path.isfile(vault_file), vault_file)
        cred = Credential(name="示例站点", username="u1", password="p1",
                          url="https://example.com", notes="备注", tags=["dev"])
        vault.add(cred)
        vault.save()
        check("保险库里有 1 条条目", len(vault.credentials) == 1,
              f"{len(vault.credentials)} 条")

        # ---------------------------------------------------- center_on_parent
        print("\n【2】center_on_parent：真实 Toplevel + 真实父窗口")
        from pm_ui.widgets import center_on_parent

        win = tk.Toplevel(root)
        created.append(win)
        tk.Label(win, text="测试弹窗").pack()
        center_on_parent(win, root)
        win.update_idletasks()
        raw = win.geometry()
        geo = parse_geometry(win)
        check("geometry() 形如 WxH+X+Y", geo is not None, repr(raw))
        if geo is not None:
            w, h, x, y = geo
            w_eff = w if w > 1 else win.winfo_reqwidth()
            h_eff = h if h > 1 else win.winfo_reqheight()
            check("X 不是 0（不再落在屏幕左上角）", x != 0, f"x={x} geometry={raw!r}")
            check("Y 不是 0（不再落在屏幕左上角）", y != 0, f"y={y} geometry={raw!r}")
            check("X 在屏幕范围内", 0 <= x and x + w_eff <= sw,
                  f"x={x} w={w_eff} 屏宽={sw}")
            check("Y 在屏幕范围内", 0 <= y and y + h_eff <= sh,
                  f"y={y} h={h_eff} 屏高={sh}")
            check("弹窗尺寸是真实请求尺寸（不是 0x0）",
                  win.winfo_reqwidth() > 1 and win.winfo_reqheight() > 1,
                  f"reqw={win.winfo_reqwidth()} reqh={win.winfo_reqheight()}")

        # 父窗口有尺寸时：以父窗口为基准居中、纵向按 0.34 偏上
        print("\n【3】center_on_parent：父窗口分支的精确坐标")
        fake = FakeParent(400, 200, 800, 600)
        win2 = tk.Toplevel(root)
        created.append(win2)
        tk.Label(win2, text="父窗口分支").pack()
        center_on_parent(win2, fake, y_ratio=0.34)
        win2.update_idletasks()
        w2 = max(win2.winfo_reqwidth(), win2.winfo_width())
        h2 = max(win2.winfo_reqheight(), win2.winfo_height())
        exp_x = max(0, min(400 + (800 - w2) // 2, max(0, sw - w2)))
        exp_y = max(0, min(200 + int((600 - h2) * 0.34), max(0, sh - h2)))
        geo2 = parse_geometry(win2)
        check("坐标等于「父窗口中心 + 纵向 0.34 偏上」",
              geo2 is not None and geo2[2] == exp_x and geo2[3] == exp_y,
              f"实际 {geo2} 期望 +{exp_x}+{exp_y}")

        # 父窗口还没映射（尺寸 1x1）时：退化为屏幕居中
        print("\n【4】center_on_parent：父窗口未映射 → 屏幕居中")
        win3 = tk.Toplevel(root)
        created.append(win3)
        tk.Label(win3, text="退化分支").pack()
        center_on_parent(win3, FakeParent(0, 0, 1, 1), y_ratio=0.34)
        win3.update_idletasks()
        w3 = max(win3.winfo_reqwidth(), win3.winfo_width())
        h3 = max(win3.winfo_reqheight(), win3.winfo_height())
        exp3 = (max(0, (sw - w3) // 2), max(0, int((sh - h3) * 0.34)))
        geo3 = parse_geometry(win3)
        check("退化为按屏幕居中（水平居中 / 纵向 0.34）",
              geo3 is not None and (geo3[2], geo3[3]) == exp3,
              f"实际 {geo3} 期望 +{exp3[0]}+{exp3[1]}")

        # ---------------------------------------------------- 回归：子代理实测出的坑
        # 未映射的父窗口在 Windows/Tk 上报的是 200x200（不是 1x1）。
        # 若只看尺寸，就会走进父窗口分支、算出负偏移、被夹成 +0+0 —— 正是要消灭的现象。
        print("\n【4b】回归：未映射父窗口自报 200x200，也不能落回 +0+0")
        win3b = tk.Toplevel(root)
        created.append(win3b)
        tk.Label(win3b, text="未映射父窗口").pack()
        center_on_parent(win3b, FakeParent(34, 57, 200, 200, viewable=False), y_ratio=0.34)
        win3b.update_idletasks()
        w3b = max(win3b.winfo_reqwidth(), win3b.winfo_width())
        h3b = max(win3b.winfo_reqheight(), win3b.winfo_height())
        exp3b = (max(0, (sw - w3b) // 2), max(0, int((sh - h3b) * 0.34)))
        geo3b = parse_geometry(win3b)
        check("未映射父窗口 → 屏幕居中，且不是 +0+0",
              geo3b is not None and (geo3b[2], geo3b[3]) == exp3b
              and not (geo3b[2] == 0 and geo3b[3] == 0),
              f"实际 {geo3b} 期望 +{exp3b[0]}+{exp3b[1]}")

        # 父窗口比弹窗还小时，同样退回屏幕居中（否则负偏移会被夹到左上角）
        print("\n【4c】父窗口装不下弹窗 → 屏幕居中")
        win3c = tk.Toplevel(root)
        created.append(win3c)
        tk.Label(win3c, text="父窗口太小").pack()
        center_on_parent(win3c, FakeParent(0, 0, 40, 40), y_ratio=0.34)
        win3c.update_idletasks()
        w3c = max(win3c.winfo_reqwidth(), win3c.winfo_width())
        h3c = max(win3c.winfo_reqheight(), win3c.winfo_height())
        exp3c = (max(0, (sw - w3c) // 2), max(0, int((sh - h3c) * 0.34)))
        geo3c = parse_geometry(win3c)
        check("父窗口装不下弹窗 → 屏幕居中",
              geo3c is not None and (geo3c[2], geo3c[3]) == exp3c,
              f"实际 {geo3c} 期望 +{exp3c[0]}+{exp3c[1]}")

        # ---------------------------------------------------- SyncDialog
        print("\n【5】真实 SyncDialog 构造")
        from pm_ui import dialogs

        dlg = None
        try:
            dlg = dialogs.SyncDialog(root, vault, "")
            created.append(dlg)
            built = True
            build_err = ""
        except Exception as exc:  # noqa: BLE001
            built, build_err = False, f"{type(exc).__name__}: {exc}"
        check("SyncDialog(parent, vault, '') 构造成功", built, build_err)

        if built:
            check("SyncDialog 是 tk.Toplevel",
                  isinstance(dlg, tk.Toplevel), type(dlg).__mro__[:2])
            dlg.update_idletasks()
            dgeom = dlg.geometry()
            check("SyncDialog 的 geometry 已被设置（非空且含 +）",
                  bool(dgeom) and "+" in dgeom, repr(dgeom))
            dgeo = parse_geometry(dlg)
            check("SyncDialog 的 geometry 能被解析成 WxH+X+Y",
                  dgeo is not None, repr(dgeom))
            if dgeo is not None:
                dw = dgeo[0] if dgeo[0] > 1 else dlg.winfo_reqwidth()
                dh = dgeo[1] if dgeo[1] > 1 else dlg.winfo_reqheight()
                check("SyncDialog 也被夹在屏幕内",
                      0 <= dgeo[2] and dgeo[2] + dw <= sw
                      and 0 <= dgeo[3] and dgeo[3] + dh <= sh,
                      f"{dgeo} 屏 {sw}x{sh}")
            dlg.destroy()
            created.remove(dlg)
            check("SyncDialog 销毁后 winfo_exists() 为假", not dlg.winfo_exists())

        # ---------------------------------------------------- 清理
        print("\n【6】清理")
        for w in list(created):
            if w.winfo_exists():
                w.destroy()
        created.clear()
        root.update_idletasks()
        root.destroy()
        # 根窗口销毁后 tk 解释器已没了，winfo_exists() 本身会抛 TclError
        try:
            still_alive = bool(root.winfo_exists())
        except tk.TclError:
            still_alive = False
        check("根窗口销毁成功", not still_alive, f"still_alive={still_alive}")
    except tk.TclError as exc:
        for w in list(created):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        if FAIL:                      # 已经跑出的失败不能被当成"环境跳过"吞掉
            print(f"  [FAIL] Tk 操作中途失败（{type(exc).__name__}: {exc}）")
            FAIL.append(f"Tk 操作失败：{exc}")
        else:
            print(f"  [skip] 本机 Tk 操作失败（{type(exc).__name__}: {exc}），跳过本测试")
            return 0
    finally:
        shutil.rmtree(WORK, ignore_errors=True)

    print("\n" + "=" * 68)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("桌面端弹窗定位测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
