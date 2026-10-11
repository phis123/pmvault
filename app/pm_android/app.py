"""Android 版入口：Flet 应用装配（含返回键处理与自动锁定）。"""

from __future__ import annotations

import time

import flet as ft

from . import dialogs as dlg
from .screens import LoginScreen, VaultScreen
from .state import APP_TITLE, AppState


def main(page: ft.Page) -> None:
    page.title = APP_TITLE
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.bgcolor = "#eef1f6"
    page.scroll = None

    state = AppState()
    state.page = page
    state.last_activity = time.time()

    root = ft.Column([], expand=True, spacing=0)
    page.add(root)

    # ------------------------------------------------------------ 屏幕切换
    def show_login() -> None:
        state.lock()
        login = LoginScreen(state, on_unlocked=on_unlocked)
        root.controls = [ft.Container(content=login.build(), alignment=ft.Alignment.TOP_CENTER, expand=True)]
        page.update()

    def on_unlocked(force_change_master: bool = False) -> None:
        state.last_activity = time.time()
        show_vault()
        if state.pending_recovery_key:
            key = state.pending_recovery_key
            state.pending_recovery_key = None
            dlg.recovery_key_dialog(page, key, "保险库已创建，这是你的恢复密钥（只显示这一次）。")
        elif force_change_master:
            dlg.info_dialog(page, "已用恢复密钥解锁", "建议尽快在「设置 → 修改主密码」里换一个新主密码。")

    def show_vault() -> None:
        vault_screen = VaultScreen(state, on_lock=show_login)
        state.current_screen = vault_screen  # type: ignore[attr-defined]
        root.controls = [vault_screen.build()]
        page.update()
        vault_screen.refresh_list()

    # ------------------------------------------------------------ 自动锁定
    async def tick() -> None:
        """空闲超时自动锁定。page.run_task 要求协程函数。"""
        import asyncio
        while True:
            await asyncio.sleep(15)
            minutes = int(state.settings.get("auto_lock_minutes", 5) or 0)
            if state.unlocked and minutes > 0 and time.time() - state.last_activity > minutes * 60:
                state.lock()
                show_login()
                dlg.info_dialog(page, "已自动锁定", "空闲超时，保险库已锁定。")

    page.run_task(tick)

    # ------------------------------------------------------------ 交互即刷新活动时间
    def touch(_e=None) -> None:
        state.last_activity = time.time()

    page.on_keyboard_event = touch
    # 搜索框、输入框变化也算活动（移动端主要输入来源）
    try:
        page.on_change = touch
    except Exception:
        pass

    # ------------------------------------------------------------ 生命周期：切到后台立即锁定
    def on_app_lifecycle(e) -> None:
        touch()
        if getattr(e, "state", None) in ("paused", "hidden", "inactive") and state.unlocked:
            # 退到后台就锁定，避免被任务切换预览看到内容
            state.lock()
            show_login()

    try:
        page.on_app_lifecycle_state_change = on_app_lifecycle
    except Exception:
        pass

    # ------------------------------------------------------------ Android 返回键
    def on_back(_e) -> None:
        # 有弹层先关弹层；详情页回列表；列表页不退出（交给系统）
        try:
            if page.overlay and getattr(page, "dialogs", None):
                pass
        except Exception:
            pass
        screen = getattr(state, "current_screen", None)
        if state.unlocked and screen is not None and screen.detail_panel.visible:
            screen.back_to_list()
            return

    try:
        page.on_back_button = on_back
    except Exception:
        pass

    show_login()


if __name__ == "__main__":
    ft.run(main)
