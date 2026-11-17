"""验证 Android 界面布局修复（不依赖设备 / Flutter 工具链）。

针对用户反馈的两个问题：
  ① 主界面与状态栏重叠  → 检查根节点被 SafeArea 包裹且四项避让都开
  ② 条目编辑框太大别扭  → 检查字段被压缩到 46dp 且走整页编辑（不再是 340x460 弹窗）

做法与 test_android_ui.py 一致：最小 Page 替身驱动真实代码路径。
"""

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")


import os
import shutil
import sys
import tempfile

BASE = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

WORK = tempfile.mkdtemp(prefix="pmlayout_")
os.environ["FLET_APP_STORAGE_DATA"] = WORK

import flet as ft  # noqa: E402

from pm_android import dialogs as dlg  # noqa: E402
from pm_android.screens import EditorScreen, LoginScreen, VaultScreen  # noqa: E402
from pm_android.state import AppState  # noqa: E402
from pm_core import Credential  # noqa: E402

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


class FakePage:
    """覆盖 app.main() 真正用到的接口。"""

    def __init__(self):
        self.overlay = []
        self.dialog_stack = []
        self.controls = []
        self.title = ""
        self.theme_mode = None
        self.padding = None
        self.bgcolor = None
        self.scroll = None
        self.on_keyboard_event = None
        self.on_change = None
        self.on_back_button = None
        self.on_app_lifecycle_state_change = None
        self.width = 412
        self.height = 915
        self.media = ft.PageMediaData(
            padding=ft.Padding.only(top=48, bottom=24, left=0, right=0),
            view_padding=ft.Padding.only(top=48, bottom=24, left=0, right=0),
            view_insets=ft.Padding.only(top=0, bottom=0, left=0, right=0),
            device_pixel_ratio=3.0,
            orientation=ft.Orientation.PORTRAIT,
        )

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self, *controls):
        pass

    def show_dialog(self, dialog):
        self.dialog_stack.append(dialog)
        self.overlay.append(dialog)

    def pop_dialog(self):
        if self.dialog_stack:
            dialog = self.dialog_stack.pop()
            if dialog in self.overlay:
                self.overlay.remove(dialog)

    def set_clipboard(self, value):
        pass

    def run_task(self, handler):
        import inspect
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("handler must be a coroutine function")


def find_fields(control, found=None):
    """递归找出所有 TextField。"""
    if found is None:
        found = []
    if isinstance(control, ft.TextField):
        found.append(control)
    for attr in ("controls", "content"):
        value = getattr(control, attr, None)
        if isinstance(value, list):
            for item in value:
                find_fields(item, found)
        elif value is not None and not isinstance(value, str):
            find_fields(value, found)
    return found


def main() -> int:
    page = FakePage()

    # ============================================================ ① 安全区
    print("\n【① 状态栏/手势条避让】")
    from pm_android import app as app_mod

    state = AppState()
    state.page = page
    try:
        app_mod.main(page)
        boot_ok = True
        boot_err = ""
    except Exception as exc:
        boot_ok = False
        boot_err = f"{type(exc).__name__}: {exc}"
    check("app.main() 能装配成功", boot_ok, boot_err)

    safe = next((c for c in page.controls if isinstance(c, ft.SafeArea)), None)
    check("根节点包在 SafeArea 里", safe is not None,
          f"page.controls 类型={[type(c).__name__ for c in page.controls]}")
    if safe is not None:
        check("避让顶部状态栏", bool(safe.avoid_intrusions_top), str(safe.avoid_intrusions_top))
        check("避让底部手势条", bool(safe.avoid_intrusions_bottom), str(safe.avoid_intrusions_bottom))
        check("避让左右（横屏刘海）",
              bool(safe.avoid_intrusions_left) and bool(safe.avoid_intrusions_right))
        check("键盘弹出不抖动（maintain_bottom_view_padding）",
              bool(safe.maintain_bottom_view_padding), str(safe.maintain_bottom_view_padding))
        check("SafeArea 撑满", bool(safe.expand), str(safe.expand))
        check("SafeArea 有内容", safe.content is not None)

    # ============================================================ ② 字段密度
    print("\n【② 输入框尺寸】")
    check("紧凑高度常量为 46（原为 Flet 默认 56）", dlg.FIELD_HEIGHT == 46, str(dlg.FIELD_HEIGHT))

    field = dlg.compact_field("测试", value="x")
    check("dense 已开", bool(field.dense), str(field.dense))
    check("高度被限制为 46", field.height == 46, str(field.height))
    check("字号 14", field.text_size == 14, str(field.text_size))
    check("标签字号 12", field.label_style is not None and field.label_style.size == 12,
          str(field.label_style))
    check("内容内边距收窄",
          field.content_padding is not None and field.content_padding.top == 8
          and field.content_padding.left == 12,
          str(field.content_padding))

    pw = dlg.compact_field("密码", password=True)
    check("password 字段带可见切换", pw.password and pw.can_reveal_password)
    ml = dlg.compact_field("备注", multiline=True, min_lines=2, max_lines=4)
    check("多行字段不设固定高度", ml.height is None, str(ml.height))

    # ============================================================ ③ 整页编辑
    print("\n【③ 条目编辑改为整页】")
    st = AppState()
    st.page = page
    from pm_core import Vault

    vault_file = os.path.join(WORK, "layout.pmvault")
    st.vault = Vault.create(vault_file, "Layout-Test-1!", device="android")
    st.vault.add(Credential(name="示例", username="u", password="p", url="https://e.com"))

    opened = {}

    def fake_edit(cred, on_saved):
        opened["cred"] = cred
        opened["on_saved"] = on_saved

    vs = VaultScreen(st, on_lock=lambda: None, on_edit=fake_edit)
    vs.build()
    vs.refresh_list()
    vs.add_entry()
    check("点「+」走整页编辑（不再是弹窗）", "cred" in opened and opened["cred"] is None,
          f"opened={list(opened)}")

    editor = EditorScreen(st, None, on_save=lambda c: None, on_cancel=lambda: None)
    built = editor.build()
    check("编辑页可构建", built is not None)
    check("编辑页是 Column（顶栏+正文+底栏）",
          isinstance(built, ft.Column) and len(built.controls) == 3, str(len(built.controls)))
    check("编辑页正文可滚动",
          editor.scroll.scroll == ft.ScrollMode.AUTO, str(editor.scroll.scroll))
    check("编辑页会自适应宽度（不再写死 340）",
          not any(getattr(c, "width", None) == 340 for c in editor.controls),
          "仍有 340 宽控件")

    fields = find_fields(built)
    check(f"编辑页里的输入框都用了紧凑高度（{len(fields)} 个）",
          bool(fields) and all(f.height in (46, None) for f in fields),
          f"异常高度: {[f.height for f in fields if f.height not in (46, None)]}")
    check("编辑页没有 7 个 56dp 大框",
          all(f.height != 56 for f in fields),
          f"高度列表: {[f.height for f in fields]}")

    # 校验逻辑仍然生效
    bad = EditorScreen(st, None, on_save=lambda c: None, on_cancel=lambda: None)
    bad.build()
    result = bad.collect()
    check("名称为空时拒绝保存并提示", result is None and bad.error.value == "名称不能为空",
          f"result={result} error={bad.error.value}")

    # 返回键
    check("编辑页返回键返回 True（已处理）", editor.handle_back() is True)
    check("保险库列表页返回键交还系统（False）", vs.handle_back() is False,
          str(vs.handle_back()))

    # ============================================================ ④ 登录页
    print("\n【④ 登录页】")
    login = LoginScreen(st, on_unlocked=lambda **kw: None)
    login.build()
    check("主密码框已收紧", bool(login.password.dense) and login.password.text_size == 15,
          f"dense={login.password.dense} size={login.password.text_size}")
    # 用户反馈「解锁按钮太大」：原先 expand=True 在 Column 里会纵向撑满整屏。
    check("解锁按钮不再纵向撑满（无 expand）",
          not login.primary_button.expand, f"expand={login.primary_button.expand}")
    check("解锁按钮高度固定为 52",
          login.primary_button.height == 52, str(login.primary_button.height))
    check("解锁按钮宽度跟随登录列宽（340）",
          login.primary_button.width == 340, str(login.primary_button.width))
    rec_btn = login.recovery_row.controls[1]
    check("恢复密钥按钮同样不纵向撑满且高度固定",
          not rec_btn.expand and rec_btn.height == 52,
          f"expand={rec_btn.expand} height={rec_btn.height}")

    # ============================================================ 清理
    shutil.rmtree(WORK, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("布局修复验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
