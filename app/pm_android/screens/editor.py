"""全屏条目编辑页（Android）。

为什么用整页而不是弹窗：
  · 条目有 7 个字段，弹窗里必然又挤又小；
  · 全屏下键盘弹出时字段不会被压扁，底部按钮固定可见；
  · 列表页/详情页/编辑页之间用 handle_back() 统一交给返回键处理。

字段样式与校验逻辑复用 dialogs.build_credential_form，只有一份实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import flet as ft

from pm_core import Credential

from .. import dialogs as dlg

if TYPE_CHECKING:  # pragma: no cover
    from ..state import AppState

MUTED = "#6b7889"


class EditorScreen:
    """新增 / 编辑条目（整页）。"""

    def __init__(self, state: "AppState", cred: Optional[Credential],
                 on_save: Callable[[Credential], None],
                 on_cancel: Callable[[], None],
                 on_delete: Optional[Callable[[Credential], None]] = None) -> None:
        self.state = state
        self.cred = cred
        self.on_save = on_save
        self.on_cancel = on_cancel
        self.on_delete = on_delete

        defaults = state.generator_defaults()
        self.controls, self.collect, self.error, self.focus_first = dlg.build_credential_form(
            state.page, defaults, cred)

        self.scroll = ft.Column(
            self.controls,
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    @property
    def page(self):
        return self.state.page

    # ================================================================ 构建
    def build(self) -> ft.Control:
        title = "编辑条目" if self.cred else "新增条目"

        save_button = ft.FilledButton("保存", icon=ft.Icons.CHECK, on_click=lambda _e: self.save())
        cancel_button = ft.TextButton("取消", on_click=lambda _e: self.cancel())

        actions = [cancel_button, save_button]
        if self.cred and self.on_delete:
            actions.insert(0, ft.TextButton("删除", on_click=lambda _e: self.ask_delete()))

        top_bar = ft.Container(
            content=ft.Row([
                ft.IconButton(ft.Icons.ARROW_BACK, tooltip="返回", on_click=lambda _e: self.cancel()),
                ft.Text(title, size=17, weight=ft.FontWeight.BOLD, expand=True),
            ], spacing=2),
            padding=ft.Padding.only(left=4, right=8, top=6, bottom=6),
            bgcolor="#ffffff",
            border=ft.Border.only(bottom=ft.BorderSide(1, "#e6ebf2")),
        )

        body = ft.Container(content=self.scroll, expand=True,
                            padding=ft.Padding.only(left=12, right=12, top=10, bottom=10),
                            bgcolor="#ffffff")

        bottom_bar = ft.Container(
            content=ft.Row(actions, alignment=ft.MainAxisAlignment.END, spacing=8),
            padding=ft.Padding.only(left=12, right=12, top=6, bottom=6),
            bgcolor="#f7f9fc",
            border=ft.Border.only(top=ft.BorderSide(1, "#e6ebf2")),
        )

        return ft.Column([top_bar, body, bottom_bar], spacing=0, expand=True)

    # ================================================================ 动作
    def save(self) -> None:
        obj = self.collect()
        if obj is None:
            return
        self.on_save(obj)

    def cancel(self) -> None:
        self.on_cancel()

    def ask_delete(self) -> None:
        if not (self.cred and self.on_delete):
            return
        cred = self.cred
        dlg.confirm_dialog(
            self.page, "删除条目",
            f"确定要删除「{cred.name}」吗？此操作不可撤销。",
            on_confirm=lambda: self.on_delete(cred),
            danger=True,
        )

    # ================================================================ 返回键
    def handle_back(self) -> bool:
        """返回键：收起键盘后回到列表。返回 True 表示已处理。"""
        try:
            if self.page.overlay:
                self.page.pop_dialog()
                return True
        except Exception:
            pass
        self.cancel()
        return True
