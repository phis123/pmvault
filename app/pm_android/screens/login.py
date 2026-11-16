"""解锁页 / 创建保险库（Android）。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import flet as ft

from pm_core import Vault, crypto

from .. import dialogs as dlg
from ..state import APP_TITLE

if TYPE_CHECKING:  # pragma: no cover
    from ..state import AppState


def _hint(text: str, color: str = "#c0392b") -> ft.Text:
    return ft.Text(text, color=color, size=13)


# 登录页内容宽度（与 root Column 一致）与按钮高度。
# 教训：按钮**不能**用 expand=True —— 在 Column 里 expand 作用于主轴（纵向），
# 会把按钮拉成一根占满整屏的竖条（用户反馈的「解锁太大了」）。
CONTENT_WIDTH = 340
BUTTON_HEIGHT = 52


class LoginScreen:
    """主密码解锁 / 首次创建 / 恢复密钥解锁。"""

    def __init__(self, state: "AppState", on_unlocked) -> None:
        self.state = state
        self.on_unlocked = on_unlocked
        self.root: "ft.Column | None" = None

        self.password = ft.TextField(
            label="主密码", password=True, can_reveal_password=True,
            autofocus=True, on_submit=lambda _e: self.submit(),
            dense=True, text_size=15,
            label_style=ft.TextStyle(size=13),
            content_padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            border_radius=10,
        )
        self.recovery = ft.TextField(
            label="恢复密钥", hint_text="ABCD-EFGH-IJKL-MNOP-QRST-UVWX",
            visible=False, on_submit=lambda _e: self.submit_recovery(),
            dense=True, text_size=14,
            label_style=ft.TextStyle(size=13),
            content_padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            border_radius=10,
        )
        self.error = _hint("")
        self.cooldown_note = ft.Text("", color="#b4761a", size=13)

        self.primary_button = ft.FilledButton(
            "解锁", on_click=lambda _e: self.submit(),
            height=BUTTON_HEIGHT, width=CONTENT_WIDTH,
        )
        # 与其它控件一样在 __init__ 里建好：重画本页时只重排、不换父节点
        self.recovery_row = ft.Column(
            [self.recovery, ft.FilledButton("用恢复密钥解锁", on_click=lambda _e: self.submit_recovery(),
                                            height=BUTTON_HEIGHT, width=CONTENT_WIDTH)],
            spacing=8, visible=False,
        )

    # ------------------------------------------------------------ 视图
    def _view_controls(self, exists: bool) -> list:
        """按「保险库是否存在」生成整页控件（build / refresh_view 共用）。"""
        title = ft.Text(APP_TITLE, size=26, weight=ft.FontWeight.BOLD)
        subtitle = ft.Text(
            "请输入主密码解锁本地保险库" if exists else "还没有保险库，请设置一个至少 8 位的主密码来创建",
            size=13, color="#6b7889",
        )
        path_note = ft.Text(
            f"保险库文件：{os.path.basename(self.state.vault_file())}", size=12, color="#98a2b3",
        )

        self.primary_button.text = "解锁" if exists else "创建并解锁"

        controls = [
            title, subtitle, path_note,
            ft.Container(height=18),
            self.password,
            self.error, self.cooldown_note,
            ft.Container(height=6),
            self.primary_button,
            ft.TextButton("用恢复密钥解锁", on_click=lambda _e: self.toggle_recovery()),
            self.recovery_row,
            ft.Container(height=24),
            ft.Text(
                "主密码不会以任何形式保存在手机里；忘记主密码时只能用恢复密钥找回。",
                size=12, color="#98a2b3", text_align=ft.TextAlign.CENTER,
            ),
        ]
        if exists:
            # 低调的文字入口：只在已有保险库（可能忘记密码）时出现
            controls.append(ft.TextButton("忘记密码？", on_click=lambda _e: self.ask_forgot_password()))
        return controls

    def build(self) -> ft.Control:
        self.root = ft.Column(
            self._view_controls(self.state.vault_exists()),
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            width=CONTENT_WIDTH,
        )
        return self.root

    def refresh_view(self) -> None:
        """按当前 state 就地重画本页。

        用于「重置保险库」之后回到创建状态：不重开 Page，
        只替换已挂到页面上的那个 Column 的内容（控件实例都复用，不换父节点）。
        """
        if self.root is None:
            return
        self.recovery_row.visible = False
        self.root.controls = self._view_controls(self.state.vault_exists())
        self.page.update()

    def toggle_recovery(self) -> None:
        self.recovery_row.visible = not self.recovery_row.visible
        self.page.update()

    @property
    def page(self):
        return self.state.page

    # ------------------------------------------------------------ 动作
    def submit(self) -> None:
        password = self.password.value or ""
        if not password:
            self.error.value = "请输入主密码"
            self.page.update()
            return
        if not self.state.vault_exists():
            self.create_vault(password)
            return
        try:
            self.state.vault = Vault.unlock(self.state.vault_file(), password)
        except crypto.WrongPasswordError:
            self.error.value = "主密码错误"
        except crypto.TamperError:
            self.error.value = "文件被篡改或损坏，已拒绝打开"
        except Exception as exc:
            self.error.value = f"打开失败：{exc}"
        else:
            self.password.value = ""
            self.on_unlocked()
            return
        self.page.update()

    def create_vault(self, password: str) -> None:
        if len(password) < 8:
            self.error.value = "主密码至少 8 位"
            self.page.update()
            return
        path = self.state.vault_file()
        try:
            if os.path.exists(path):
                os.replace(path, path + ".bak")
            vault = Vault.create(path, password, device="android")
        except Exception as exc:
            self.error.value = f"创建失败：{exc}"
            self.page.update()
            return
        self.state.vault = vault
        self.state.pending_recovery_key = vault.last_recovery_key
        self.password.value = ""
        self.on_unlocked()

    def submit_recovery(self) -> None:
        key = self.recovery.value or ""
        if not key.strip():
            self.error.value = "请输入恢复密钥"
            self.page.update()
            return
        try:
            self.state.vault = Vault.unlock_with_recovery_key(self.state.vault_file(), key)
        except Exception as exc:
            self.error.value = str(exc)
            self.page.update()
            return
        self.recovery.value = ""
        self.on_unlocked(force_change_master=True)

    # ------------------------------------------------------------ 忘记密码 → 重置
    def ask_forgot_password(self) -> None:
        """第 1 步：先讲清「两把钥匙」，再给「继续（去重置）」入口。"""
        dlg.forget_password_dialog(
            self.page,
            on_continue=self.ask_reset_vault,
            on_use_recovery=self.toggle_recovery,
        )

    def ask_reset_vault(self) -> None:
        """第 2 步：强确认弹窗（要手打「重置」两个字）。"""
        dlg.reset_vault_dialog(self.page, on_reset=self.do_reset_vault)

    def do_reset_vault(self) -> None:
        """真正删掉旧保险库，并把本页重画回「创建新保险库」状态。"""
        try:
            self.state.reset_vault()
        except Exception as exc:
            self.error.value = f"重置失败：{exc}"
            self.page.update()
            return
        self.password.value = ""
        self.recovery.value = ""
        self.error.value = ""
        self.cooldown_note.value = ""
        self.refresh_view()
