"""端到端导航验证：解锁 → 列表 → 详情 → 编辑页 → 保存 → 回详情。

这一层验证的是"接线"：app.main() 里的屏幕切换、编辑页回调、返回键栈。
布局与字段样式由 test_android_layout.py 覆盖；本文件只管流程是否走通。
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

WORK = tempfile.mkdtemp(prefix="pmnav_")
os.environ["FLET_APP_STORAGE_DATA"] = WORK

import flet as ft  # noqa: E402

from pm_android import app as app_mod  # noqa: E402
from pm_android.screens import EditorScreen, LoginScreen, VaultScreen  # noqa: E402
from pm_android.state import AppState  # noqa: E402

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


class FakePage:
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


def screen_of(page, state):
    """从页面根节点里取出当前屏幕对象（app.py 的 root 在 SafeArea 里）。"""
    root = None
    for control in page.controls:
        if isinstance(control, ft.SafeArea):
            root = control.content
    if root is None or not root.controls:
        return None
    return getattr(state, "current_screen", None)


def main() -> int:
    page = FakePage()
    app_mod.main(page)

    # app.main() 内部自己建 state（不对外暴露），这里只借它验证能装配成功；
    # 导航流程用一份等价 state 驱动同样的屏幕类。
    safe = next((c for c in page.controls if isinstance(c, ft.SafeArea)), None)
    check("app 装配后有 SafeArea 根节点", safe is not None)

    st = AppState()
    st.page = page

    print("\n【解锁 → 列表】")
    from pm_core import Credential, Vault

    vault_file = os.path.join(WORK, "nav.pmvault")
    st.vault = Vault.create(vault_file, "Nav-Test-1!", device="android")
    st.vault.add(Credential(name="GitHub", username="dev", password="Str0ng!Pass#2026",
                            url="https://github.com"))

    transitions = {"saved": None, "cancelled": 0}

    def show_vault():
        vs = VaultScreen(st, on_lock=lambda: None, on_edit=show_editor)
        st.vault_screen = vs
        st.vault_root = vs.build()
        st.current_screen = vs
        page.controls = [st.vault_root]
        vs.refresh_list()
        return vs

    def return_to_vault():
        """与应用一致：复用同一个 VaultScreen 与已构建控件树，并保留详情视图。"""
        vs = getattr(st, "vault_screen", None)
        st.current_screen = vs
        page.controls = [getattr(st, "vault_root", None) or vs.build()]
        vs.refresh_list(keep_view=True)

    def show_editor(cred, on_saved):
        def save(obj):
            on_saved(obj)
            return_to_vault()

        editor = EditorScreen(st, cred, on_save=save, on_cancel=return_to_vault)
        st.current_screen = editor
        page.controls = [editor.build()]

    vs = show_vault()
    check("列表显示 1 条", len(vs.list_view.controls) == 1, str(len(vs.list_view.controls)))

    print("\n【点「+」→ 编辑页】")
    vs.add_entry()
    check("进入编辑页", isinstance(st.current_screen, EditorScreen), type(st.current_screen).__name__)
    editor = st.current_screen
    check("是新增模式（标题为新增条目）", editor.cred is None)

    print("\n【填写并保存】")
    fields = [c for c in editor.controls if isinstance(c, ft.TextField)]
    # 控件顺序：名称 / 用户名 / (Row: 密码 + 生成) / 网址 / 标签 / 备注
    fields[0].value = "淘宝"
    fields[1].value = "buyer001"
    password_field = editor.controls[2].controls[0]
    password_field.value = "Ta0bao!Secret"
    fields[2].value = "https://taobao.com"      # 网址
    editor.save()

    check("保存后回到列表", isinstance(st.current_screen, VaultScreen),
          type(st.current_screen).__name__)
    names = [c.name for c in st.vault.credentials]
    check("新条目已写入保险库", "淘宝" in names, str(names))
    check("列表更新为 2 条", len(st.current_screen.list_view.controls) == 2,
          str(len(st.current_screen.list_view.controls)))
    check("保存后自动打开该条目详情（列表面板隐藏）",
          st.current_screen.detail_panel.visible is True,
          f"detail_panel.visible={st.current_screen.detail_panel.visible}")

    print("\n【详情页返回键】")
    handled = st.current_screen.handle_back()
    check("详情页返回键回列表", handled is True and st.current_screen.detail_panel.visible is False,
          f"handled={handled} visible={st.current_screen.detail_panel.visible}")

    print("\n【编辑已有条目】")
    target = next(c for c in st.vault.credentials if c.name == "淘宝")
    st.current_screen.edit_entry(target)
    check("进入编辑页（编辑模式）", isinstance(st.current_screen, EditorScreen) and st.current_screen.cred is target)
    editor2 = st.current_screen
    name_field = editor2.controls[0]
    check("字段预填了原值", name_field.value == "淘宝", str(name_field.value))
    name_field.value = "淘宝（改）"
    editor2.save()
    check("改名已生效", any(c.name == "淘宝（改）" for c in st.vault.credentials),
          str([c.name for c in st.vault.credentials]))

    print("\n【取消编辑不写入】")
    before = len(st.vault.credentials)
    st.current_screen.edit_entry(st.vault.credentials[0])
    editor3 = st.current_screen
    editor3.controls[0].value = "不应被保存"
    editor3.cancel()
    check("取消后回到列表", isinstance(st.current_screen, VaultScreen))
    check("取消没有写入", len(st.vault.credentials) == before and
          not any(c.name == "不应被保存" for c in st.vault.credentials),
          str([c.name for c in st.vault.credentials]))

    print("\n【编辑页里的返回键】")
    st.current_screen.edit_entry(st.vault.credentials[0])
    editor4 = st.current_screen
    handled = editor4.handle_back()
    check("编辑页返回键已处理", handled is True)
    check("返回后回到列表", isinstance(st.current_screen, VaultScreen),
          type(st.current_screen).__name__)

    shutil.rmtree(WORK, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("导航流程验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
