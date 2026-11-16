"""Android 版入口：Flet 应用装配（含安全区、返回键处理与自动锁定）。"""

from __future__ import annotations

import time
from typing import Optional

import flet as ft

from . import dialogs as dlg
from .screens import EditorScreen, LoginScreen, VaultScreen
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

    # 关键：Android 15（targetSdk 36）强制全屏边到边，不用 SafeArea 的话
    # 标题会被状态栏压住、底部状态行会被手势条压住。
    # maintain_bottom_view_padding 让键盘弹出时布局不抖动。
    page.add(ft.SafeArea(
        content=root,
        expand=True,
        avoid_intrusions_top=True,
        avoid_intrusions_bottom=True,
        avoid_intrusions_left=True,
        avoid_intrusions_right=True,
        maintain_bottom_view_padding=True,
    ))

    # ------------------------------------------------------------ 屏幕切换
    def show_login() -> None:
        state.lock()
        login = LoginScreen(state, on_unlocked=on_unlocked)
        state.current_screen = login  # type: ignore[attr-defined]
        root.controls = [ft.Container(content=login.build(), alignment=ft.Alignment.CENTER, expand=True)]
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

    def show_vault(select_id: Optional[str] = None) -> None:
        vault_screen = VaultScreen(state, on_lock=show_login, on_edit=show_editor)
        state.vault_screen = vault_screen  # type: ignore[attr-defined]
        state.current_screen = vault_screen  # type: ignore[attr-defined]
        state.vault_root = vault_screen.build()  # type: ignore[attr-defined]
        root.controls = [state.vault_root]  # type: ignore[attr-defined]
        page.update()
        vault_screen.refresh_list()
        if select_id:
            for cred in state.vault.credentials:
                if cred.id == select_id:
                    vault_screen.open_detail(cred)
                    break

    # ------------------------------------------------------------ 条目编辑（整页）
    def return_to_vault() -> None:
        """从编辑页回到保险库。

        复用同一个 VaultScreen 实例与已构建的控件树（不重新 build），
        这样调用方在保存后设置的详情页状态不会被冲掉。
        """
        vault_screen = getattr(state, "vault_screen", None)
        if vault_screen is None:
            show_vault()
            return
        state.current_screen = vault_screen  # type: ignore[attr-defined]
        root.controls = [getattr(state, "vault_root", None) or vault_screen.build()]
        page.update()
        # keep_view：保存后要停在详情页，别被刷回列表
        vault_screen.refresh_list(keep_view=True)

    def show_editor(cred, on_saved) -> None:
        """打开整页编辑器。on_saved(obj) 负责把条目写进保险库。"""

        def save(obj) -> None:
            on_saved(obj)          # 写入 + 可能切到详情页
            return_to_vault()      # 复用控件树返回，不覆盖上面的状态

        editor = EditorScreen(state, cred, on_save=save, on_cancel=return_to_vault)
        state.current_screen = editor  # type: ignore[attr-defined]
        root.controls = [editor.build()]
        page.update()

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
        # 优先级：当前屏自己处理（编辑页收起键盘/返回；详情页回列表）→ 否则交给系统
        screen = getattr(state, "current_screen", None)
        if screen is not None and hasattr(screen, "handle_back") and screen.handle_back():
            return
        if state.unlocked and screen is not None and getattr(screen, "detail_panel", None) is not None \
                and screen.detail_panel.visible:
            screen.back_to_list()
            return

    try:
        page.on_back_button = on_back
    except Exception:
        pass

    show_login()


if __name__ == "__main__":
    ft.run(main)
