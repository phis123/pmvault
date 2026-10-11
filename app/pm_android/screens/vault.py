"""主界面（Android）：条目列表 + 详情 + 顶部操作。"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

import flet as ft

from pm_core import Credential, crypto

from .. import dialogs as dlg
from ..state import APP_TITLE

if TYPE_CHECKING:  # pragma: no cover
    from ..state import AppState

MUTED = "#6b7889"
FAINT = "#98a2b3"


class VaultScreen:
    def __init__(self, state: "AppState", on_lock) -> None:
        self.state = state
        self.on_lock = on_lock

        self.search = ft.TextField(
            hint_text="搜索名称 / 用户名 / 网址 / 标签",
            prefix_icon=ft.Icons.SEARCH, dense=True, expand=True,
            on_change=lambda _e: self.refresh_list(),
        )
        self.list_view = ft.ListView(expand=True, spacing=0)
        self.detail_view = ft.Column([], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self.status = ft.Text("", size=12, color=FAINT)

        self.detail_panel = ft.Container(content=self.detail_view, expand=True, padding=12, visible=False)
        self.list_panel = ft.Column([
            self.list_view,
        ], expand=True, visible=True)

    # ================================================================ 顶层
    def build(self) -> ft.Control:
        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            items=[
                ft.PopupMenuItem(content=ft.Text("加密同步"), icon=ft.Icons.SYNC,
                                 on_click=lambda _e: self.open_sync()),
                ft.PopupMenuItem(content=ft.Text("密码生成器"), icon=ft.Icons.AUTO_AWESOME,
                                 on_click=lambda _e: self.open_generator()),
                ft.PopupMenuItem(content=ft.Text("设置"), icon=ft.Icons.SETTINGS,
                                 on_click=lambda _e: self.open_settings()),
                ft.PopupMenuItem(content=ft.Text("锁定"), icon=ft.Icons.LOCK,
                                 on_click=lambda _e: self.on_lock()),
            ],
        )

        self.top_bar = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(APP_TITLE, size=17, weight=ft.FontWeight.BOLD),
                    menu,
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Row([self.search,
                        ft.IconButton(ft.Icons.ADD, tooltip="新增条目", on_click=lambda _e: self.add_entry())]),
            ], spacing=6),
            padding=ft.Padding.only(left=12, right=6, top=8, bottom=6),
            bgcolor="#ffffff",
            border=ft.Border.only(bottom=ft.BorderSide(1, "#e6ebf2")),
        )

        self.body = ft.Column([self.list_panel], expand=True)
        return ft.Column([
            self.top_bar,
            self.body,
            ft.Container(content=self.status, padding=ft.Padding.only(left=12, right=12, top=4, bottom=6),
                         bgcolor="#f7f9fc"),
        ], spacing=0, expand=True)

    # ================================================================ 列表
    def refresh_list(self) -> None:
        page = self.state.page
        vault = self.state.vault
        if vault is None:
            return
        query = (self.search.value or "").strip()
        entries = vault.search(query)
        self.list_view.controls.clear()

        if not entries:
            self.list_view.controls.append(
                ft.Container(content=ft.Text("没有匹配的条目" if query else "还没有条目，点右上「+」新增",
                                             color=FAINT, text_align=ft.TextAlign.CENTER),
                             padding=30))
        for cred in entries:
            self.list_view.controls.append(self._entry_tile(cred))

        summary = self.state.summary()
        self.status.value = (f"条目 {summary['count']} · 版本 v{summary['revision']} · KDF {summary['kdf']} · "
                             f"弱密码 {summary['weak']} · 过期 {summary['old']}")
        self.show_list()
        page.update()

    def _entry_tile(self, cred: Credential) -> ft.Control:
        flags = self.state.markers(cred)
        badge_row = [ft.Container(
            content=ft.Text(text, size=10, color=color),
            padding=ft.Padding.symmetric(horizontal=5, vertical=1),
            border_radius=99, bgcolor="#eef1f6",
        ) for text, color in flags]
        subtitle_parts = [cred.username] if cred.username else []
        if cred.tags:
            subtitle_parts.append("· " + ", ".join(cred.tags))
        return ft.Container(
            content=ft.ListTile(
                leading=ft.Icon(ft.Icons.KEY, color="#2f6fed"),
                title=ft.Text(cred.name, weight=ft.FontWeight.W_500),
                subtitle=ft.Text(" ".join(subtitle_parts) or "—", size=12, color=MUTED),
                trailing=ft.Row(badge_row, spacing=4, tight=True) if badge_row else None,
                on_click=lambda _e, c=cred: self.open_detail(c),
            ),
            border=ft.Border.only(bottom=ft.BorderSide(1, "#eef1f6")),
            bgcolor="#ffffff",
        )

    # ================================================================ 视图切换
    def show_list(self) -> None:
        self.list_panel.visible = True
        self.detail_panel.visible = False
        if self.list_panel not in self.body.controls:
            self.body.controls = [self.list_panel]
        self.state.selected_id = None

    def show_detail(self) -> None:
        self.list_panel.visible = False
        self.detail_panel.visible = True
        if self.detail_panel not in self.body.controls:
            self.body.controls = [self.detail_panel]

    # ================================================================ 详情
    def open_detail(self, cred: Credential) -> None:
        self.state.selected_id = cred.id
        self.detail_view.controls.clear()

        rows: List[ft.Control] = [
            ft.Row([
                ft.IconButton(ft.Icons.ARROW_BACK, tooltip="返回列表", on_click=lambda _e: self.back_to_list()),
                ft.Text(cred.name, size=19, weight=ft.FontWeight.BOLD, expand=True),
            ]),
        ]
        if cred.url:
            rows.append(ft.Text(cred.url, size=13, color="#2f6fed", selectable=True))

        if cred.username:
            rows.append(ft.Row([
                ft.Text("用户名", width=64, size=13, color=MUTED),
                ft.Text(cred.username, selectable=True, expand=True),
                ft.IconButton(ft.Icons.COPY, tooltip="复制用户名",
                              on_click=lambda _e: dlg.copy_text(self.state.page, cred.username, "用户名")),
            ]))

        # 密码行：默认掩码，点「显示」才展示（只在内存里放，切换列表就清掉）
        revealed = self.state.revealed.get(cred.id)
        password_text = revealed if revealed else "●" * min(len(cred.password), 14)
        rows.append(ft.Row([
            ft.Text("密码", width=64, size=13, color=MUTED),
            ft.Text(password_text, font_family="monospace", selectable=bool(revealed), expand=True),
            ft.IconButton(
                ft.Icons.VISIBILITY_OFF if revealed else ft.Icons.VISIBILITY,
                tooltip="隐藏密码" if revealed else "显示密码",
                on_click=lambda _e, c=cred: self.toggle_reveal(c),
            ),
            ft.IconButton(ft.Icons.COPY, tooltip="复制密码",
                          on_click=lambda _e, c=cred: dlg.copy_text(self.state.page, c.password, "密码")),
        ]))

        strength = crypto.password_strength(cred.password)
        rows.append(ft.Row([
            ft.Text("强度", width=64, size=13, color=MUTED),
            ft.ProgressBar(value=(strength["score"] + 1) / 5, width=140,
                           color=["#dc2626", "#ea580c", "#ca8a04", "#16a34a", "#15803d"][strength["score"]]),
            ft.Text(f"{strength['label']}（{strength['entropy']} bit）", size=12, color=MUTED),
        ]))

        rows.append(ft.Row([
            ft.FilledButton("编辑", icon=ft.Icons.EDIT, on_click=lambda _e, c=cred: self.edit_entry(c)),
            ft.OutlinedButton("删除", icon=ft.Icons.DELETE_OUTLINE, on_click=lambda _e, c=cred: self.delete_entry(c)),
        ], spacing=8))

        if cred.tags:
            rows.append(ft.Row([
                ft.Text("标签", width=64, size=13, color=MUTED),
                ft.Row([ft.Chip(label=ft.Text(t)) for t in cred.tags], wrap=True, expand=True),
            ]))
        if cred.notes:
            rows.append(ft.Column([
                ft.Text("备注", size=13, color=MUTED),
                ft.Container(content=ft.Text(cred.notes, selectable=True), padding=10, border_radius=8,
                             bgcolor="#f7f9fc"),
            ], spacing=4))
        if cred.custom_fields:
            field_rows = []
            for item in cred.custom_fields:
                value = item.get("value", "")
                if item.get("secret"):
                    value = "●" * min(len(value), 12)
                field_rows.append(ft.Row([
                    ft.Text(item.get("label", "字段"), width=84, size=13, color=MUTED),
                    ft.Text(value, selectable=not item.get("secret"), expand=True),
                ]))
            rows.append(ft.Column([ft.Text("自定义字段", size=13, color=MUTED), *field_rows], spacing=4))

        warnings = []
        if cred.password and strength["score"] <= 1:
            warnings.append("密码强度偏弱")
        dup = self.state.duplicate_names(cred)
        if dup:
            warnings.append("与 " + "、".join(dup) + " 使用相同密码")
        if cred.age_days() > self.state.old_days():
            warnings.append(f"密码已使用 {cred.age_days()} 天，建议更换")
        if warnings:
            rows.append(ft.Container(
                content=ft.Column([ft.Text("⚠ " + w, size=13, color="#9a3412") for w in warnings]),
                padding=10, border_radius=8, bgcolor="#fff7ed",
            ))

        rows.append(ft.Container(height=12))
        self.detail_view.controls.extend(rows)
        self.show_detail()
        self.state.page.update()

    def back_to_list(self) -> None:
        self.state.selected_id = None
        self.state.revealed.clear()
        self.refresh_list()

    def toggle_reveal(self, cred: Credential) -> None:
        if cred.id in self.state.revealed:
            self.state.revealed.pop(cred.id, None)
        else:
            self.state.revealed[cred.id] = cred.password
        self.open_detail(cred)

    # ================================================================ 动作
    def add_entry(self) -> None:
        dlg.credential_dialog(self.state.page, self.state.generator_defaults(), None,
                             on_save=self._save_new)

    def _save_new(self, cred: Credential) -> None:
        try:
            self.state.vault.add(cred)
        except Exception as exc:
            dlg.info_dialog(self.state.page, "保存失败", str(exc))
            return
        self.refresh_list()

    def edit_entry(self, cred: Credential) -> None:
        dlg.credential_dialog(self.state.page, self.state.generator_defaults(), cred,
                             on_save=self._save_edit, on_delete=self.delete_entry)

    def _save_edit(self, cred: Credential) -> None:
        try:
            self.state.vault.update(cred)
        except Exception as exc:
            dlg.info_dialog(self.state.page, "保存失败", str(exc))
            return
        self.state.revealed.pop(cred.id, None)
        self.open_detail(cred)
        self.refresh_list()
        self.open_detail(cred)

    def delete_entry(self, cred: Credential) -> None:
        def do_delete() -> None:
            try:
                self.state.vault.delete(cred.id)
            except Exception as exc:
                dlg.info_dialog(self.state.page, "删除失败", str(exc))
                return
            self.state.revealed.pop(cred.id, None)
            self.refresh_list()

        dlg.confirm_dialog(self.state.page, "删除条目",
                           f"确定要删除「{cred.name}」吗？此操作不可撤销。",
                           on_confirm=do_delete, danger=True)

    def open_generator(self) -> None:
        dlg.generator_dialog(self.state.page, self.state.generator_defaults(),
                             on_use=lambda value: dlg.copy_text(self.state.page, value, "生成的密码"))

    def open_sync(self) -> None:
        dlg.sync_dialog(self.state.page, self.state, on_imported=self._on_sync_imported)

    def _on_sync_imported(self, creds, meta) -> None:
        self.state.revealed.clear()
        self.refresh_list()
        dlg.info_dialog(self.state.page, "同步完成",
                        f"已导入 {len(creds)} 条（远端版本 v{meta.get('revision')}）。")

    def open_settings(self) -> None:
        dlg.settings_dialog(self.state.page, self.state,
                            on_saved=self._on_settings_saved,
                            on_change_master=self.open_change_master,
                            on_rotate_recovery=self.rotate_recovery)

    def _on_settings_saved(self, updated) -> None:
        from ..state import save_settings
        self.state.settings = updated
        save_settings(updated)
        self.refresh_list()

    def open_change_master(self) -> None:
        dlg.change_master_dialog(self.state.page, self.state, on_done=self._after_change_master)

    def _after_change_master(self, recovery_key: Optional[str]) -> None:
        if recovery_key:
            dlg.recovery_key_dialog(self.state.page, recovery_key, "主密码已修改，这是新的恢复密钥。")
        else:
            dlg.info_dialog(self.state.page, "完成", "主密码已修改。")

    def rotate_recovery(self) -> None:
        def do_rotate() -> None:
            try:
                key = self.state.vault.rotate_recovery_key()
            except Exception as exc:
                dlg.info_dialog(self.state.page, "操作失败", str(exc))
                return
            dlg.recovery_key_dialog(self.state.page, key, "旧恢复密钥已失效，这是新的恢复密钥。")

        dlg.confirm_dialog(self.state.page, "重新生成恢复密钥",
                           "旧恢复密钥会立即失效，确定继续吗？", on_confirm=do_rotate, danger=True)
