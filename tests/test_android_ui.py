"""深度验证 pm_android 界面逻辑（不依赖 Flutter 工具链）。

做法：写一个最小 Page 替身，提供 update/show_dialog/pop_dialog/overlay/set_clipboard 等，
用它驱动 LoginScreen / VaultScreen / 各弹层的真实代码路径，验证：
- 页面能构建
- 创建保险库 → 新增 → 列表 → 详情 → 编辑 → 删除 全通
- 显示/隐藏密码、搜索、标记、统计、锁定
- 各类弹层都能构建成功（构建错会抛异常）

真实会话的启动验证另有 verify_flet_ui.py（web 模式跑起来、零异常）。
"""

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")


import asyncio
import os
import shutil
import sys
import tempfile

BASE = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

WORK = tempfile.mkdtemp(prefix="pmand_")
os.environ["FLET_APP_STORAGE_DATA"] = WORK

import flet as ft  # noqa: E402

from pm_android import dialogs as dlg  # noqa: E402
from pm_android.screens import LoginScreen, VaultScreen  # noqa: E402
from pm_android.state import AppState  # noqa: E402
from pm_core import Credential  # noqa: E402

FAIL = []
CLIPBOARD = {"text": ""}


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


class FakePage:
    """最小 Page 替身：只实现界面代码真正用到的那部分。"""

    def __init__(self):
        self.overlay = []
        self.update_count = 0
        self.dialog_stack = []
        self.title = ""
        self.theme_mode = None
        self.padding = None
        self.bgcolor = None
        self.scroll = None
        self.controls = []
        self.on_keyboard_event = None
        self.on_change = None
        self.on_back_button = None
        self.on_app_lifecycle_state_change = None

    def update(self, *controls):
        self.update_count += 1

    def show_dialog(self, dialog):
        self.dialog_stack.append(dialog)
        self.overlay.append(dialog)

    def pop_dialog(self):
        if self.dialog_stack:
            dialog = self.dialog_stack.pop()
            if dialog in self.overlay:
                self.overlay.remove(dialog)

    def set_clipboard(self, value: str):
        CLIPBOARD["text"] = value

    def run_task(self, handler):
        # 不真正跑后台任务，只验证它是个协程函数（这是之前踩过的坑）
        if not asyncio.iscoroutinefunction(handler):
            raise TypeError("handler must be a coroutine function")
        return None


def main() -> int:
    page = FakePage()
    st = AppState()
    st.page = page

    from pm_android.screens import LoginScreen as LS  # 已导入，保持显式

    # ---------------------------------------------------------- 解锁页
    unlocked_calls = []
    login = LS(st, on_unlocked=lambda **kw: unlocked_calls.append(kw))
    login.build()
    check("解锁页可构建", login.primary_button.text == "创建并解锁", f"按钮={login.primary_button.text}")

    # 空密码拒绝
    login.password.value = ""
    login.submit()
    check("空主密码被拒绝", login.error.value == "请输入主密码", login.error.value)

    # 太短拒绝
    login.password.value = "123"
    login.submit()
    check("过短主密码被拒绝", "至少 8 位" in login.error.value, login.error.value)

    # 正常创建
    login.password.value = "Android-Test-1!"
    login.submit()
    check("可以创建保险库", st.unlocked, "未解锁")
    check("创建后获得恢复密钥", bool(st.pending_recovery_key), str(st.pending_recovery_key))
    check("解锁回调被调用", bool(unlocked_calls))
    check("保险库文件存在", st.vault_exists())

    # ---------------------------------------------------------- 主界面
    vault_screen = VaultScreen(st, on_lock=lambda: None)
    vault_screen.build()
    vault_screen.refresh_list()
    check("空列表给出提示", len(vault_screen.list_view.controls) == 1)

    st.vault.add(Credential(name="GitHub", username="alice", password="pw-1", tags=["dev"],
                            url="https://github.com", notes="两步验证已开"))
    st.vault.add(Credential(name="邮箱", username="bob", password="pw-1", tags=["mail"]))
    st.vault.add(Credential(name="旧账号", username="carol", password="123456"))
    vault_screen.refresh_list()
    check("列表显示 3 条", len(vault_screen.list_view.controls) == 3,
          f"实际 {len(vault_screen.list_view.controls)}")

    github = [c for c in st.vault.credentials if c.name == "GitHub"][0]

    # ---------------------------------------------------------- 详情页
    vault_screen.open_detail(github)
    check("详情页可见", vault_screen.detail_panel.visible and not vault_screen.list_panel.visible)
    check("详情页有足够内容", len(vault_screen.detail_view.controls) >= 6,
          f"{len(vault_screen.detail_view.controls)} 个控件")

    flags = st.markers(github)
    check("重复密码标记生效", any(f[0] == "重" for f in flags), str(flags))
    weak = st.markers([c for c in st.vault.credentials if c.name == "旧账号"][0])
    check("弱密码标记生效", any(f[0] == "弱" for f in weak), str(weak))

    vault_screen.toggle_reveal(github)
    check("可显示明文密码", st.revealed.get(github.id) == "pw-1")
    vault_screen.toggle_reveal(github)
    check("可隐藏密码", github.id not in st.revealed)

    vault_screen.back_to_list()
    check("可返回列表", vault_screen.list_panel.visible and not vault_screen.detail_panel.visible)

    # ---------------------------------------------------------- 搜索
    vault_screen.search.value = "github"
    vault_screen.refresh_list()
    check("搜索过滤生效", len(vault_screen.list_view.controls) == 1,
          f"实际 {len(vault_screen.list_view.controls)}")
    vault_screen.search.value = "dev"
    vault_screen.refresh_list()
    check("按标签搜索生效", len(vault_screen.list_view.controls) == 1)
    vault_screen.search.value = ""
    vault_screen.refresh_list()

    # ---------------------------------------------------------- 复制到剪贴板
    dlg.copy_text(page, "pw-1", "密码")
    check("复制写入剪贴板", CLIPBOARD["text"] == "pw-1", CLIPBOARD["text"])
    page.pop_dialog()

    # ---------------------------------------------------------- 编辑 / 删除
    github.username = "alice2"
    github.password = "BrandNew-9!"
    st.vault.update(github)
    check("编辑后数据更新", st.vault.get(github.id).username == "alice2"
          and st.vault.get(github.id).password == "BrandNew-9!")
    vault_screen.refresh_list()
    check("编辑后重复标记消失", not any(f[0] == "重" for f in st.markers(st.vault.get(github.id))))

    st.vault.delete(github.id)
    vault_screen.refresh_list()
    check("删除后剩 2 条", len(vault_screen.list_view.controls) == 2,
          f"实际 {len(vault_screen.list_view.controls)}")

    # ---------------------------------------------------------- 各弹层构建
    def try_dialog(label, fn):
        try:
            fn()
            built = len(page.dialog_stack) > 0
            page.pop_dialog()
            check(label, built, "没有进入弹层栈")
        except Exception as exc:
            check(label, False, f"{type(exc).__name__}: {exc}")

    try_dialog("信息弹层可构建", lambda: dlg.info_dialog(page, "标题", "内容"))
    try_dialog("生成器弹层可构建", lambda: dlg.generator_dialog(page, st.generator_defaults(), lambda v: None))
    try_dialog("设置弹层可构建", lambda: dlg.settings_dialog(
        page, st, on_saved=lambda s: None, on_change_master=lambda: None, on_rotate_recovery=lambda: None))
    try_dialog("同步弹层可构建", lambda: dlg.sync_dialog(page, st, on_imported=lambda c, m: None))
    try_dialog("恢复密钥弹层可构建",
               lambda: dlg.recovery_key_dialog(page, "ABCD-EFGH-IJKL-MNOP-QRST-UVWX", "测试"))
    try_dialog("条目编辑弹层可构建",
               lambda: dlg.credential_dialog(page, st.generator_defaults(), None, on_save=lambda c: None))
    try_dialog("条目编辑（编辑模式）可构建",
               lambda: dlg.credential_dialog(page, st.generator_defaults(),
                                             st.vault.credentials[0], on_save=lambda c: None,
                                             on_delete=lambda c: None))
    try_dialog("修改主密码弹层可构建",
               lambda: dlg.change_master_dialog(page, st, on_done=lambda k: None))

    # ---------------------------------------------------------- 同步往返
    from pm_core import sync
    lines = sync.pack(st.vault.credentials, "sync-pass-1", st.vault.header.revision, st.vault.header.device)
    data = sync.unpack(lines, "sync-pass-1")
    check("同步字符串往返一致", len(data["credentials"]) == 2, str(len(data["credentials"])))
    check("同步字符串里无明文", "邮箱" not in "".join(lines))

    # ---------------------------------------------------------- 统计 / 锁定
    summary = st.summary()
    check("统计正确", summary["count"] == 2 and summary["weak"] >= 1, str(summary))
    st.lock()
    check("锁定清空内存", st.vault is None and not st.revealed)

    # 锁定后重新解锁（走真实核心层）
    login2 = LoginScreen(st, on_unlocked=lambda **kw: None)
    login2.build()
    login2.password.value = "Android-Test-1!"
    login2.submit()
    check("可以重新解锁", st.unlocked and len(st.vault.credentials) == 2,
          f"unlocked={st.unlocked}")
    login2.password.value = "wrong-password"
    st.lock()
    login2.submit()
    check("错误主密码被拒绝", not st.unlocked and login2.error.value == "主密码错误", login2.error.value)

    # 恢复密钥解锁（用创建时拿到的那把）
    saved_key = st.pending_recovery_key or ""
    login3 = LoginScreen(st, on_unlocked=lambda **kw: None)
    login3.build()
    login3.recovery.value = saved_key
    login3.submit_recovery()
    check("恢复密钥可解锁", st.unlocked, "未解锁")

    shutil.rmtree(WORK, ignore_errors=True)
    print()
    if FAIL:
        print(f"Android 界面验证失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("Android 界面验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
