"""解锁页 / 创建保险库（Android）。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import flet as ft

from pm_core import Vault, crypto

from ..state import APP_TITLE

if TYPE_CHECKING:  # pragma: no cover
    from ..state import AppState


def _hint(text: str, color: str = "#c0392b") -> ft.Text:
    return ft.Text(text, color=color, size=13)


class LoginScreen:
    """主密码解锁 / 首次创建 / 恢复密钥解锁。"""

    def __init__(self, state: "AppState", on_unlocked) -> None:
        self.state = state
        self.on_unlocked = on_unlocked

        self.password = ft.TextField(
            label="主密码", password=True, can_reveal_password=True,
            autofocus=True, on_submit=lambda _e: self.submit(),
        )
        self.recovery = ft.TextField(
            label="恢复密钥", hint_text="ABCD-EFGH-IJKL-MNOP-QRST-UVWX",
            visible=False, on_submit=lambda _e: self.submit_recovery(),
        )
        self.error = _hint("")
        self.cooldown_note = ft.Text("", color="#b4761a", size=13)

        self.primary_button = ft.FilledButton("解锁", on_click=lambda _e: self.submit(), width=320)

    # ------------------------------------------------------------ 视图
    def build(self) -> ft.Control:
        exists = self.state.vault_exists()
        title = ft.Text(APP_TITLE, size=26, weight=ft.FontWeight.BOLD)
        subtitle = ft.Text(
            "请输入主密码解锁本地保险库" if exists else "还没有保险库，请设置一个至少 8 位的主密码来创建",
            size=13, color="#6b7889",
        )
        path_note = ft.Text(
            f"保险库文件：{os.path.basename(self.state.vault_file())}", size=12, color="#98a2b3",
        )

        buttons = [
            self.primary_button,
            ft.TextButton("用恢复密钥解锁", on_click=lambda _e: self.toggle_recovery()),
        ]
        self.recovery_row = ft.Column(
            [self.recovery, ft.FilledButton("用恢复密钥解锁", on_click=lambda _e: self.submit_recovery(), width=320)],
            spacing=8, visible=False,
        )

        self.primary_button.text = "解锁" if exists else "创建并解锁"

        return ft.Column(
            [
                ft.Container(height=48),
                title, subtitle, path_note,
                ft.Container(height=18),
                self.password,
                self.error, self.cooldown_note,
                ft.Container(height=6),
                *buttons,
                self.recovery_row,
                ft.Container(height=24),
                ft.Text(
                    "主密码不会以任何形式保存在手机里；忘记主密码时只能用恢复密钥找回。",
                    size=12, color="#98a2b3", text_align=ft.TextAlign.CENTER,
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            width=360,
        )

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
