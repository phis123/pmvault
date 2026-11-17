"""验证 Android 版「忘记密码 → 重置保险库（直接删除）」（不依赖设备 / Flutter 工具链）。

覆盖：
  ① 解锁页「忘记密码？」入口只在该有保险库时出现
  ② 第 1 步说明弹窗（两把钥匙 / 恢复密钥示例 / 两个按钮的行为）
  ③ 第 2 步强确认弹窗（必须手打「重置」两个字按钮才可用）
  ④ 执行重置：文件与 .bak 被删、内存状态被清空
  ⑤ 界面回到「创建新保险库」状态（主按钮文案变「创建并解锁」）
  ⑥ 安全回归：重置后新建的保险库是全新密文，旧主密码打不开
  ⑦ reset_vault 的返回值、幂等性与 docstring 里的安全声明

做法与 test_android_layout.py 一致：最小 Page 替身驱动真实代码路径。
数据目录自建在 work\\_testtmp\\ 下并自行清理——本机 %TEMP% 禁止创建文件，不用 tempfile。
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

BASE = _APP_DIR
LIBS = _APP_DIR
for p in (BASE, LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)

# 自建独立数据目录：不碰真实数据，也不用 tempfile（%TEMP% 不可写）
WORK_ROOT = _WORK_ROOT
WORK = os.path.join(WORK_ROOT, f"pmreset_{os.getpid()}")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)
os.environ["FLET_APP_STORAGE_DATA"] = WORK

import flet as ft  # noqa: E402

from pm_android import dialogs as dlg  # noqa: E402
from pm_android.screens import LoginScreen  # noqa: E402
from pm_android.state import AppState  # noqa: E402
from pm_core import Credential, Vault, crypto  # noqa: E402

OLD_PW = "Old-Master-Pass-1!"
NEW_PW = "New-Master-Pass-2!"
RESET_WORD = "重置"

FAIL = []


def check(label, ok, detail=""):
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAIL.append(label)


class FakePage:
    """覆盖界面代码真正用到的接口。"""

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


# ---------------------------------------------------------------- 控件树小工具

def walk(control):
    """递归遍历控件树（controls / content / actions 三个方向）。"""
    yield control
    for attr in ("controls", "content", "actions"):
        value = getattr(control, attr, None)
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif value is not None and not isinstance(value, str):
            yield from walk(value)


def current_dialog(page):
    return page.dialog_stack[-1] if page.dialog_stack else None


def all_text(control) -> str:
    """把控件树里所有 Text 的内容拼起来（用来断言文案）。"""
    return "\n".join(c.value for c in walk(control) if isinstance(c, ft.Text) and isinstance(c.value, str))


def button_label(control):
    """按钮文案：FilledButton 用 text，TextButton 用 content（Flet 1.0 两者不同）。"""
    text = getattr(control, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(control, "content", None)
    return content if isinstance(content, str) else None


def find_button(control, text):
    for c in walk(control):
        if type(c).__name__ in ("TextButton", "FilledButton", "ElevatedButton"):
            if button_label(c) == text:
                return c
    return None


def find_field(control, label_part):
    for c in walk(control):
        if isinstance(c, ft.TextField) and label_part in (c.label or ""):
            return c
    return None


def password_rejected(path, password) -> bool:
    """用某个主密码去开某个库，确认被拒（不是别的异常）。"""
    try:
        Vault.unlock(path, password)
    except crypto.WrongPasswordError:
        return True
    except Exception:
        return False
    return False


def main() -> int:
    page = FakePage()
    st = AppState()
    st.page = page
    vf = st.vault_file()

    # ============================================================ ① 入口显隐
    print("\n【① 「忘记密码？」入口的显隐】")
    unlocked_calls = []
    login = LoginScreen(st, on_unlocked=lambda **kw: unlocked_calls.append(kw))
    built = login.build()
    check("还没有保险库时不出现入口", find_button(built, "忘记密码？") is None)
    check("没有保险库时主按钮文案为「创建并解锁」",
          login.primary_button.text == "创建并解锁", str(login.primary_button.text))

    st.vault = Vault.create(vf, OLD_PW, device="android")
    st.vault.add(Credential(name="GitHub", username="alice", password="Str0ng!Pass#2026"))
    check("前置：保险库文件已生成", os.path.exists(vf))
    check("前置：.bak 也已生成（保存过两次）", os.path.exists(vf + ".bak"))

    login = LoginScreen(st, on_unlocked=lambda **kw: unlocked_calls.append(kw))
    built = login.build()
    entry = find_button(built, "忘记密码？")
    check("已有保险库时出现入口", entry is not None)
    check("入口是低调的文字按钮（TextButton，不是大按钮）",
          type(entry).__name__ == "TextButton", type(entry).__name__)
    check("已有保险库时主按钮文案为「解锁」", login.primary_button.text == "解锁",
          str(login.primary_button.text))
    check("入口排在主按钮之后（页面底部）",
          entry is not None and built.controls.index(entry) > built.controls.index(login.primary_button))

    # ============================================================ ② 第 1 步
    print("\n【② 第 1 步：说明弹窗】")
    entry.on_click(None)
    step1 = current_dialog(page)
    check("点入口能打开第 1 步弹窗", step1 is not None)
    check("标题为「忘记主密码？」",
          step1 is not None and step1.title.value == "忘记主密码？",
          "" if step1 is None else str(step1.title.value))
    body1 = all_text(step1) if step1 is not None else ""
    check("提到恢复密钥仍可打开保险库", "恢复密钥" in body1)
    check("给出恢复密钥的形态示例", "ABCD-EFGH-IJKL-MNOP-QRST-UVWX" in body1)
    check("说明两把钥匙都丢失时无法恢复", "密码学上无法恢复" in body1)
    recovery_btn = find_button(step1, "我还能找到恢复密钥")
    go_btn = find_button(step1, "继续")
    check("按钮 1「我还能找到恢复密钥」存在", recovery_btn is not None)
    check("按钮 2「继续」存在", go_btn is not None)

    check("点击前恢复密钥输入框是收起的", login.recovery_row.visible is False)
    recovery_btn.on_click(None)
    check("按钮 1 关闭了弹窗", not page.dialog_stack, str(len(page.dialog_stack)))
    check("按钮 1 顺带展开了恢复密钥输入框", login.recovery_row.visible is True)

    login.ask_forgot_password()          # 重新打开第 1 步
    step1 = current_dialog(page)
    find_button(step1, "继续").on_click(None)
    step2 = current_dialog(page)
    check("「继续」切到第 2 步（第 1 步已关闭）", step2 is not None and step2 is not step1)

    # ============================================================ ③ 第 2 步
    print("\n【③ 第 2 步：强确认弹窗】")
    check("标题为「重置保险库」",
          step2 is not None and step2.title.value == "重置保险库",
          "" if step2 is None else str(step2.title.value))
    body2 = all_text(step2) if step2 is not None else ""
    check("明确写出旧保险库立即删除、条目全部丢失",
          "重置后旧保险库立即删除，其中的条目全部丢失" in body2, body2[:60])
    confirm = find_field(step2, RESET_WORD)
    reset_btn = find_button(step2, "重置保险库")
    cancel_btn = find_button(step2, "取消")
    check("有确认输入框", confirm is not None)
    check("输入框 label 为「请输入「重置」确认」",
          confirm is not None and confirm.label == "请输入「重置」确认",
          "" if confirm is None else str(confirm.label))
    check("有「重置保险库」按钮", reset_btn is not None)
    check("刚打开时确认按钮不可用", reset_btn is not None and reset_btn.disabled is True,
          "" if reset_btn is None else str(reset_btn.disabled))
    check("取消按钮始终可用", cancel_btn is not None and not cancel_btn.disabled)

    for wrong in ("删除", "reset", "重 置", "重置保险库", "重置!"):
        confirm.value = wrong
        confirm.on_change(None)
        check(f"输入「{wrong}」时确认按钮仍不可用", reset_btn.disabled is True)

    for good in ("重置", "  重置  ", "\t重置\n", " 重置\t "):
        confirm.value = good
        confirm.on_change(None)
        check(f"输入 {good!r}（去空格后等于「重置」）时按钮可用", reset_btn.disabled is False,
              str(reset_btn.disabled))

    # 未确认时点击按钮：不能有任何破坏动作
    confirm.value = "不重置"
    confirm.on_change(None)
    reset_btn.on_click(None)
    check("未输入「重置」时点击按钮不执行重置（弹窗还在、文件还在）",
          bool(page.dialog_stack) and os.path.exists(vf))
    check("未输入「重置」时内存里的 vault 还在", st.vault is not None)

    # ============================================================ ④ 执行重置
    print("\n【④ 执行重置（破坏性删除）】")
    old_bytes = open(vf, "rb").read()
    old_salt = st.vault.header.salt
    old_id = st.vault.credentials[0].id
    st.revealed = {old_id: "Str0ng!Pass#2026"}
    st.selected_id = old_id
    st.pending_recovery_key = "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF"

    confirm.value = "  重置 "
    confirm.on_change(None)
    reset_btn.on_click(None)

    check("确认后弹窗已关闭", not page.dialog_stack, str(len(page.dialog_stack)))
    check("vault_exists() 变为 False", st.vault_exists() is False)
    check("state.vault 置为 None", st.vault is None)
    check("vault 文件已删除", not os.path.exists(vf))
    check("vault 的 .bak 已删除", not os.path.exists(vf + ".bak"))
    check("selected_id 已清空", st.selected_id is None)
    check("revealed 已清空", not st.revealed, str(st.revealed))
    check("pending_recovery_key 已清空", st.pending_recovery_key is None)
    try:
        Vault.unlock(vf, OLD_PW)
        gone = False
    except Exception:
        gone = True
    check("重置后旧主密码再也打不开（文件不存在，也无处恢复）", gone)

    # ============================================================ ⑤ 回到创建状态
    print("\n【⑤ 界面回到「创建新保险库」状态】")
    check("主按钮文案变为「创建并解锁」", login.primary_button.text == "创建并解锁",
          str(login.primary_button.text))
    check("「忘记密码？」入口消失", find_button(built, "忘记密码？") is None)
    check("重画的正是 app.py 已挂上去的那个 Column", login.root is built)
    check("恢复密钥输入框回到收起状态", login.recovery_row.visible is False)
    check("错误提示已清空", login.error.value == "")
    check("输入框里没有残留的旧主密码", not (login.password.value or ""))

    # ============================================================ ⑥ 安全回归
    print("\n【⑥ 安全回归：重置后的新库是全新密文，旧主密码无效】")
    login.password.value = NEW_PW
    login.submit()
    check("重置后可以创建全新保险库", st.unlocked and st.vault_exists())
    check("新库是空的（旧条目没有复活）",
          st.vault is not None and len(st.vault.credentials) == 0)
    new_bytes = open(vf, "rb").read()
    check("新库密文与旧库密文逐字节不同", new_bytes != old_bytes)
    check("新库 salt 已换（不是旧库那把）",
          st.vault is not None and st.vault.header.salt != old_salt)
    check("旧主密码打不开新保险库", password_rejected(vf, OLD_PW))
    check("新主密码可以打开新保险库", not password_rejected(vf, NEW_PW))

    # ============================================================ ⑦ 返回值 / 幂等
    print("\n【⑦ reset_vault 的返回值、幂等与安全声明】")
    st.lock()
    st.reset_vault()                     # 先把 ⑥ 造的库清掉，腾出位置造「vault + .bak」
    st.vault = Vault.create(vf, "Third-Pass-3!", device="android")
    st.vault.add(Credential(name="示例", username="u", password="p"))
    check("前置：vault 与 .bak 同时存在", os.path.exists(vf) and os.path.exists(vf + ".bak"))
    removed = st.reset_vault()
    check("返回两个被删除的路径（含 .bak）",
          sorted(removed) == sorted([vf, vf + ".bak"]), str(removed))
    check("两个文件都已被删除", not os.path.exists(vf) and not os.path.exists(vf + ".bak"))

    st.selected_id = "leftover"
    st.revealed = {"leftover": "secret"}
    st.pending_recovery_key = "LEFTOVER-KEY"
    again = st.reset_vault()
    check("幂等：文件都不存在时返回空列表且不抛异常", again == [], str(again))
    check("幂等：仍清空 selected_id / revealed / pending_recovery_key",
          st.selected_id is None and not st.revealed and st.pending_recovery_key is None)
    check("幂等：vault_exists() 仍为 False", st.vault_exists() is False)

    doc = AppState.reset_vault.__doc__ or ""
    check("docstring 写明这是不可恢复的破坏性操作", "不可恢复的破坏性操作" in doc)
    check("docstring 写明不存在「绕过主密码」的合法途径",
          "不存在「绕过主密码」的合法途径" in doc)
    check("重置确认词常量为「重置」", dlg.RESET_WORD == RESET_WORD, str(dlg.RESET_WORD))

    # ============================================================ 清理
    shutil.rmtree(WORK, ignore_errors=True)
    try:
        os.rmdir(WORK_ROOT)
    except OSError:
        pass

    print("\n" + "=" * 60)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for item in FAIL:
            print("  -", item)
        return 1
    print("忘记密码 → 重置保险库 验证全部通过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        # 中途失败也要把自建的数据目录收掉
        shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(code)
