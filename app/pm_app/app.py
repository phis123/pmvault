"""PMVault 双端统一主入口（Windows 桌面 + Android 手机/平板）。"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import flet as ft

from .screens.login import LoginScreen
from .screens.onboarding import MandatoryResetScreen, RecoveryDisplayScreen, V1UpgradeScreen
from .screens.vault_view import VaultScreen
from .state import APP_TITLE, AppState


def main(page: ft.Page) -> None:
    state = AppState()
    state.page = page

    page.title = APP_TITLE
    page.padding = 0
    page.spacing = 0

    # 桌面端使用仿宋字体（Windows）；移动端沿用系统默认无衬线字体
    if os.name == "nt" and not os.environ.get("FLET_APP_STORAGE_DATA"):
        page.theme = ft.Theme(font_family="FangSong")
        page.dark_theme = ft.Theme(font_family="FangSong")
        try:
            page.window.width = 1140
            page.window.height = 720
            page.window.min_width = 360
            page.window.min_height = 540
        except Exception:
            pass

    state.apply_theme()

    vault_screen: Optional[VaultScreen] = None
    running = True

    def _set_view(ctrl: ft.Control) -> None:
        page.controls.clear()
        page.add(ctrl)
        page.update()

    def _show_login() -> None:
        nonlocal vault_screen
        vault_screen = None
        state.lock()
        state.apply_theme()
        screen = LoginScreen(
            state,
            on_unlocked=_show_vault,
            on_need_v1_upgrade=_show_v1_upgrade,
            on_need_recovery_reset=_show_mandatory_reset,
            on_vault_created=lambda rk, kf_note: _show_recovery_display(
                title="密码库已创建，请保存一次性恢复码",
                subtitle="这是您忘记主密码或丢失本机密钥时的唯一兜底凭证。",
                recovery_key=rk,
                keyfile_note=kf_note,
            ),
        )
        _set_view(screen.build())

    def _show_recovery_display(title: str, subtitle: str, recovery_key: str, keyfile_note: str = "") -> None:
        screen = RecoveryDisplayScreen(
            state,
            title=title,
            subtitle=subtitle,
            recovery_key=recovery_key,
            keyfile_note=keyfile_note,
            on_continue=_show_vault,
        )
        _set_view(screen.build())

    def _show_v1_upgrade() -> None:
        screen = V1UpgradeScreen(
            state,
            on_upgraded=lambda new_rk, kf_note: _show_recovery_display(
                title="旧版密码库已升级为新版",
                subtitle="原文件已自动保留 .v1.bak 备份，旧恢复码已失效。请妥善保存以下全新一次性恢复码：",
                recovery_key=new_rk,
                keyfile_note=kf_note,
            ),
            on_cancel=_show_login,
        )
        _set_view(screen.build())

    def _show_mandatory_reset() -> None:
        screen = MandatoryResetScreen(
            state,
            on_reset_done=lambda new_rk, kf_note: _show_recovery_display(
                title="主密码已重设，请保存新的一次性恢复码",
                subtitle="您刚才使用的旧恢复码已彻底作废。请立即保存下面全新生成的一次性恢复码：",
                recovery_key=new_rk,
                keyfile_note=kf_note,
            ),
            on_cancel=_show_login,
        )
        _set_view(screen.build())

    def _show_vault() -> None:
        nonlocal vault_screen
        state.apply_theme()
        vault_screen = VaultScreen(
            state,
            on_lock=_show_login,
            on_show_new_recovery=lambda title, rk: _show_recovery_display(
                title=title,
                subtitle="旧恢复码已立即失效，请妥善保存以下全新一次性恢复码。",
                recovery_key=rk,
            ),
            on_theme_changed=lambda: _show_vault(),
        )
        _set_view(vault_screen.build())

    # 响应式窗口尺寸变化
    def _on_resize(_e) -> None:
        if vault_screen is not None:
            vault_screen._render_layout()

    page.on_resize = _on_resize

    # 键盘快捷键（Windows 桌面）+ Esc 返回
    def _on_keyboard(e: ft.KeyboardEvent) -> None:
        if vault_screen is None:
            return
        state.touch_activity()
        key = (e.key or "").lower()
        if e.ctrl and key == "f":
            vault_screen.focus_search()
        elif e.ctrl and key == "n":
            vault_screen.open_new_entry()
        elif e.ctrl and key == "l":
            _show_login()
        elif key == "escape":
            vault_screen.handle_back()

    page.on_keyboard_event = _on_keyboard

    # 后台轻量心跳：每秒刷新 2FA 动态码倒计时 & 检测无操作自动锁定
    async def _ticker() -> None:
        while running:
            await asyncio.sleep(1.0)
            if state.vault is not None:
                if state.is_idle_expired():
                    _show_login()
                    state.toast("长时间无操作，已自动锁定密码库")
                elif vault_screen is not None:
                    vault_screen.tick_totp()

    def _on_disconnect(_e) -> None:
        nonlocal running
        running = False

    page.on_disconnect = _on_disconnect
    _show_login()
    try:
        page.run_task(_ticker)
    except Exception:
        pass
