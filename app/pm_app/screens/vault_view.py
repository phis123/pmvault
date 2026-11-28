"""响应式三断点主界面：
- 宽屏（>= 960px，Windows 桌面 / 平板横屏）：左分类导航 + 中条目列表 + 右详情/编辑
- 中屏（600–959px，平板竖屏 / 桌面小窗）：左条目列表 + 右详情/编辑，分类收进侧边抽屉
- 窄屏（< 600px，手机竖屏）：单栏栈式导航（列表 ↔ 详情 ↔ 编辑），支持系统返回键
"""

from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from pm_core import Credential, sorting
from ..state import AppState
from ..theme import Palette, layout_mode, pill
from .detail_view import DetailView
from .dialogs import (
    close_dlg,
    open_dual_export_dialog,
    open_generator_dialog,
    open_import_dialog,
    open_settings_dialog,
    show_dlg,
)
from .editor_view import EditorView


class VaultScreen:
    def __init__(
        self,
        state: AppState,
        *,
        on_lock: Callable[[], None],
        on_show_new_recovery: Callable[[str, str], None],
        on_theme_changed: Callable[[], None],
    ) -> None:
        self.state = state
        self.on_lock = on_lock
        self.on_show_new_recovery = on_show_new_recovery
        self.on_theme_changed = on_theme_changed

        # 窄屏当前子页：'list' | 'detail' | 'editor'
        self._sub_view: str = "list"
        self._right_mode: str = "detail"  # 'detail' | 'editor'
        self._rendered_layout_key: Optional[tuple] = None

        self.search_tf = ft.TextField(
            hint_text="输入关键词后按回车搜索",
            dense=True,
            expand=True,
            on_submit=self._on_search_submit,
            suffix=ft.Row(
                [
                    ft.IconButton(ft.Icons.SEARCH, icon_size=18, tooltip="搜索", on_click=self._on_search_submit),
                    ft.IconButton(ft.Icons.CLEAR, icon_size=18, tooltip="清空", on_click=self._on_search_clear),
                ],
                spacing=0,
                tight=True,
            ),
        )

        self.detail = DetailView(
            state,
            on_edit=self.open_editor,
            on_changed=self.refresh,
            on_back=self.back_to_list,
        )
        self.editor = EditorView(
            state,
            on_saved=self._on_editor_saved,
            on_cancel=self._on_editor_cancel,
        )

        self._sidebar_col = ft.Column([], spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self._list_view = ft.ListView(expand=True, spacing=2, padding=ft.Padding.symmetric(horizontal=8, vertical=6))
        self._status_bar = ft.Text("", size=11, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self._root = ft.Container(expand=True)

    def build(self) -> ft.Control:
        self.refresh()
        return self._root

    def tick_totp(self) -> None:
        if self._right_mode == "detail":
            self.detail.tick_totp()

    def focus_search(self) -> None:
        try:
            self.search_tf.focus()
        except Exception:
            pass

    def open_new_entry(self) -> None:
        self.open_editor(None)

    def open_editor(self, cred: Optional[Credential]) -> None:
        self.state.touch_activity()
        self._right_mode = "editor"
        self._sub_view = "editor"
        self.editor.load(cred)
        self._render_layout()

    def back_to_list(self) -> None:
        self._sub_view = "list"
        self._right_mode = "detail"
        self._render_layout()

    def handle_back(self) -> bool:
        """响应 Android 系统返回键或 Esc，已处理返回 True。"""
        if self._sub_view in ("editor", "detail") and self._current_mode() == "narrow":
            self.back_to_list()
            return True
        if self._right_mode == "editor":
            self._right_mode = "detail"
            self._render_layout()
            return True
        return False

    def _on_editor_saved(self, cred: Credential) -> None:
        self.state.selected_id = cred.id
        self._right_mode = "detail"
        self._sub_view = "detail"
        self.refresh()

    def _on_editor_cancel(self) -> None:
        self._right_mode = "detail"
        if self._current_mode() == "narrow":
            self._sub_view = "detail" if self.state.selected_id else "list"
        self._render_layout()

    def _on_search_submit(self, _e=None) -> None:
        self.state.touch_activity()
        self.state.search_query = (self.search_tf.value or "").strip()
        self.refresh()

    def _on_search_clear(self, _e=None) -> None:
        self.state.touch_activity()
        self.search_tf.value = ""
        self.state.search_query = ""
        self.refresh()

    def _current_mode(self) -> str:
        w = getattr(self.state.page, "width", None) or 1080
        return layout_mode(w)

    # ------------------------------------------------------------ 数据刷新 + 布局重绘
    def refresh(self) -> None:
        vault = self.state.vault
        if vault is None:
            return
        pal = self.state.palette()
        old_days = self.state.old_days()
        stats = vault.stats(old_days=old_days)

        # 1. 重建侧边分类导航
        nav_items = [
            ("all", "全部账号", ft.Icons.ALL_INBOX_OUTLINED, stats["count"]),
            ("favorites", "收藏", ft.Icons.STAR_OUTLINE, stats["favorites"]),
            ("totp", "2FA 动态码", ft.Icons.VERIFIED_USER_OUTLINED, stats["totp_count"]),
            ("weak", "弱密码", ft.Icons.Gpp_BAD_OUTLINED if hasattr(ft.Icons, "Gpp_BAD_OUTLINED") else ft.Icons.WARNING_AMBER, stats["weak"]),
            ("duplicate", "重复密码", ft.Icons.CONTENT_COPY, stats["duplicate_count"]),
            ("old", f"过期 (>{old_days}天)", ft.Icons.SCHEDULE, stats["old"]),
            ("trash", "回收站", ft.Icons.DELETE_OUTLINE, stats["trash_count"]),
        ]

        def _close_drawer_if_open() -> None:
            page = self.state.page
            if page is not None and getattr(page, "drawer", None) is not None:
                try:
                    page.run_task(page.close_drawer)
                except Exception:
                    pass

        def _select_cat(cat: str, tag: str = "") -> None:
            self.state.touch_activity()
            self.state.category = cat
            self.state.active_tag = tag
            _close_drawer_if_open()
            self.refresh()

        def _build_nav_controls() -> list[ft.Control]:
            items: list[ft.Control] = [
                ft.Container(
                    padding=ft.Padding.only(left=12, right=12, top=14, bottom=8),
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.SHIELD, color=pal.primary, size=20),
                            ft.Text(stats["vault_name"], size=15, weight=ft.FontWeight.BOLD, color=pal.text, expand=True),
                        ],
                        spacing=8,
                    ),
                ),
            ]
            for cat_id, label, icon, cnt in nav_items:
                active = self.state.category == cat_id and not self.state.active_tag
                items.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(icon, size=18, color=pal.primary if active else pal.text_muted),
                                ft.Text(label, size=13, weight=ft.FontWeight.W_600 if active else ft.FontWeight.NORMAL, color=pal.primary if active else pal.text, expand=True),
                                ft.Text(str(cnt), size=11, color=pal.primary if active else pal.text_faint),
                            ],
                            spacing=10,
                        ),
                        padding=ft.Padding.symmetric(horizontal=12, vertical=9),
                        margin=ft.Margin.symmetric(horizontal=8, vertical=1),
                        border_radius=8,
                        bgcolor=pal.selected_bg if active else None,
                        on_click=lambda _e, c=cat_id: _select_cat(c),
                    )
                )
            tags_local = vault.all_tags()
            if tags_local:
                items.append(ft.Divider(height=16, color=pal.border))
                items.append(
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=14, vertical=4),
                        content=ft.Text("标签", size=11, weight=ft.FontWeight.W_600, color=pal.text_faint),
                    )
                )
                for tag_name, cnt in tags_local:
                    active = self.state.active_tag == tag_name
                    items.append(
                        ft.Container(
                            content=ft.Row(
                                [
                                    ft.Icon(ft.Icons.LABEL_OUTLINE, size=16, color=pal.primary if active else pal.text_muted),
                                    ft.Text(tag_name, size=13, color=pal.primary if active else pal.text, expand=True),
                                    ft.Text(str(cnt), size=11, color=pal.text_faint),
                                ],
                                spacing=8,
                            ),
                            padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                            margin=ft.Margin.symmetric(horizontal=8, vertical=1),
                            border_radius=8,
                            bgcolor=pal.selected_bg if active else None,
                            on_click=lambda _e, t=tag_name: _select_cat("all", t),
                        )
                    )
            return items

        self._build_nav_controls_fn = _build_nav_controls
        self._sidebar_col.controls = _build_nav_controls()

        # 2. 重建条目列表
        entries = vault.search(
            self.state.search_query,
            sort_key=self.state.sort_key(),
            sort_desc=self.state.sort_desc(),
            category=self.state.category,
            tag=self.state.active_tag,
            old_days=old_days,
        )

        dup_hashes = vault._duplicate_password_hashes()
        tiles: list[ft.Control] = []
        if self.state.category == "trash" and entries:
            def _empty(_e) -> None:
                n = vault.empty_trash()
                self.state.toast(f"已清空回收站（{n} 条）")
                self.refresh()

            tiles.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=6, vertical=4),
                    content=ft.Row(
                        [
                            ft.Text("回收站中的条目可恢复或彻底删除", size=12, color=pal.text_muted, expand=True),
                            ft.TextButton("清空回收站", on_click=_empty),
                        ]
                    ),
                )
            )

        if not entries:
            tiles.append(
                ft.Container(
                    padding=36,
                    alignment=ft.Alignment(0, 0),
                    content=ft.Text(
                        "没有匹配的条目" if self.state.search_query else "当前分类暂无条目",
                        size=13,
                        color=pal.text_faint,
                    ),
                )
            )
        else:
            for cred in entries:
                tiles.append(self._build_entry_tile(cred, pal, old_days, dup_hashes))

        self._list_view.controls = tiles

        # 3. 同步详情页数据
        if self._right_mode == "detail":
            selected = vault.get(self.state.selected_id) if self.state.selected_id else None
            if selected is None and entries and self._current_mode() != "narrow":
                selected = entries[0]
                self.state.selected_id = selected.id
            self.detail.set_credential(selected, show_back=(self._current_mode() == "narrow"))

        # 4. 状态栏
        self._status_bar.value = (
            f"{stats['vault_name']} · {stats['count']} 条 · 弱 {stats['weak']} · 重 {stats['duplicate_count']} · "
            f"旧 {stats['old']} · 排序 {sorting.sort_label(self.state.sort_key(), self.state.sort_desc())}"
        )
        self._status_bar.color = pal.text_faint

        self._render_layout()

    def _build_entry_tile(self, cred: Credential, pal: Palette, old_days: int, dup_hashes: set) -> ft.Control:
        selected = cred.id == self.state.selected_id and self._current_mode() != "narrow"
        markers = self.state.vault.markers(cred, old_days=old_days, dup_hashes=dup_hashes) if self.state.vault else []

        sub_parts = [cred.username] if cred.username else []
        if cred.totp_secret:
            sub_parts.append("2FA")
        if cred.api_keys:
            sub_parts.append(f"{len(cred.api_keys)} 密钥")
        if cred.tags:
            sub_parts.append("· " + ", ".join(cred.tags[:2]))

        badges = [
            pill(txt, col, pal.surface_alt, size=10) for txt, col in markers
        ]
        if cred.favorite:
            badges.insert(0, ft.Icon(ft.Icons.STAR, size=14, color=pal.warning))

        def _pick(_e, cid=cred.id) -> None:
            self.state.touch_activity()
            self.state.selected_id = cid
            self._right_mode = "detail"
            self._sub_view = "detail"
            self.refresh()

        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Text((cred.name[:1] or "?").upper(), size=14, weight=ft.FontWeight.BOLD, color=pal.primary if selected else pal.text_muted),
                        width=36,
                        height=36,
                        border_radius=10,
                        bgcolor=pal.primary_soft if selected else pal.surface_alt,
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Column(
                        [
                            ft.Text(cred.name, size=14, weight=ft.FontWeight.W_600, color=pal.text, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(" ".join(sub_parts) or "—", size=12, color=pal.text_muted, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Row(badges, spacing=4, tight=True),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=10,
            bgcolor=pal.selected_bg if selected else pal.surface,
            border=ft.Border.all(1, pal.primary if selected else pal.border),
            on_click=_pick,
        )

    # ------------------------------------------------------------ 顶部工具栏与响应式装配
    def _open_sort_dialog(self) -> None:
        page = self.state.page
        if page is None:
            return
        rg_key = ft.RadioGroup(
            value=self.state.sort_key(),
            content=ft.Column([ft.Radio(value=k, label=sorting.SORT_LABELS[k]) for k in sorting.SORT_KEYS], spacing=4),
        )
        rg_dir = ft.RadioGroup(
            value="desc" if self.state.sort_desc() else "asc",
            content=ft.Row([ft.Radio(value="asc", label="递增"), ft.Radio(value="desc", label="递减")]),
        )

        def _apply(_e) -> None:
            self.state.set_sort(rg_key.value or sorting.DEFAULT_SORT_KEY, rg_dir.value == "desc")
            close_dlg(page, dlg)
            self.refresh()

        dlg = ft.AlertDialog(
            title=ft.Text("排序方式", weight=ft.FontWeight.W_600),
            content=ft.Column([rg_key, ft.Divider(), rg_dir], tight=True, spacing=8),
            actions=[
                ft.TextButton("取消", on_click=lambda _e: close_dlg(page, dlg)),
                ft.FilledButton("应用", on_click=_apply),
            ],
        )
        show_dlg(page, dlg)

    def _build_action_menu(self) -> ft.PopupMenuButton:
        return ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            tooltip="更多操作",
            items=[
                ft.PopupMenuItem(content=ft.Text("排序方式"), icon=ft.Icons.SWAP_VERT, on_click=lambda _e: self._open_sort_dialog()),
                ft.PopupMenuItem(content=ft.Text("双通道导出（文件+二维码）"), icon=ft.Icons.QR_CODE_2, on_click=lambda _e: open_dual_export_dialog(self.state)),
                ft.PopupMenuItem(content=ft.Text("导入同步文件 / 密码库"), icon=ft.Icons.DOWNLOAD, on_click=lambda _e: open_import_dialog(self.state, on_done=lambda _k: self.refresh())),
                ft.PopupMenuItem(content=ft.Text("密码生成器"), icon=ft.Icons.AUTO_AWESOME, on_click=lambda _e: open_generator_dialog(self.state)),
                ft.PopupMenuItem(
                    content=ft.Text("设置与安全"),
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    on_click=lambda _e: open_settings_dialog(
                        self.state,
                        on_theme_or_setting_changed=self.on_theme_changed,
                        on_show_new_recovery=self.on_show_new_recovery,
                    ),
                ),
                ft.PopupMenuItem(content=ft.Text("锁定 / 切换密码库"), icon=ft.Icons.LOCK_OUTLINE, on_click=lambda _e: self.on_lock()),
            ],
        )

    def _build_list_column(self, pal: Palette, *, show_drawer_btn: bool) -> ft.Control:
        top_controls: list[ft.Control] = []
        if show_drawer_btn:
            async def _open_drawer(_e) -> None:
                page = self.state.page
                if page is None:
                    return
                nav_fn = getattr(self, "_build_nav_controls_fn", None)
                nav_items = nav_fn() if nav_fn else []
                page.drawer = ft.NavigationDrawer(
                    bgcolor=pal.surface,
                    controls=[ft.SafeArea(content=ft.Column(nav_items, spacing=2))],
                )
                page.update()
                try:
                    await page.show_drawer()
                except Exception:
                    pass

            top_controls.append(ft.IconButton(ft.Icons.MENU, tooltip="分类与标签", on_click=_open_drawer))
        top_controls.extend(
            [
                self.search_tf,
                ft.IconButton(ft.Icons.ADD, tooltip="新增账号 (Ctrl+N)", icon_color=pal.primary, on_click=lambda _e: self.open_new_entry()),
                self._build_action_menu(),
            ]
        )
        return ft.Column(
            [
                ft.Container(
                    content=ft.Row(top_controls, spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    bgcolor=pal.surface,
                    border=ft.Border.only(bottom=ft.BorderSide(1, pal.border)),
                ),
                self._list_view,
                ft.Container(
                    content=self._status_bar,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    bgcolor=pal.surface_alt,
                    border=ft.Border.only(top=ft.BorderSide(1, pal.border)),
                ),
            ],
            spacing=0,
            expand=True,
        )

    def _render_layout(self, *, force: bool = False) -> None:
        pal = self.state.palette()
        mode = self._current_mode()
        layout_key = (mode, self._right_mode, self._sub_view, pal.bg)
        if not force and self._rendered_layout_key == layout_key and self._root.content is not None:
            if self.state.page:
                self.state.page.update()
            return
        self._rendered_layout_key = layout_key
        right_panel = self.editor.control() if self._right_mode == "editor" else self.detail.control()

        if mode == "wide":
            # 宽屏：三栏（左侧导航 210px + 中间列表 340px + 右侧详情/编辑自适应）
            body = ft.Row(
                [
                    ft.Container(
                        width=210,
                        bgcolor=pal.surface,
                        border=ft.Border.only(right=ft.BorderSide(1, pal.border)),
                        content=self._sidebar_col,
                    ),
                    ft.Container(
                        width=340,
                        bgcolor=pal.bg,
                        border=ft.Border.only(right=ft.BorderSide(1, pal.border)),
                        content=self._build_list_column(pal, show_drawer_btn=False),
                    ),
                    ft.Container(expand=True, bgcolor=pal.bg, content=right_panel),
                ],
                spacing=0,
                expand=True,
            )
        elif mode == "medium":
            # 中屏（平板竖屏 / 桌面小窗）：双栏 + 抽屉导航
            body = ft.Row(
                [
                    ft.Container(
                        width=300,
                        bgcolor=pal.bg,
                        border=ft.Border.only(right=ft.BorderSide(1, pal.border)),
                        content=self._build_list_column(pal, show_drawer_btn=True),
                    ),
                    ft.Container(expand=True, bgcolor=pal.bg, content=right_panel),
                ],
                spacing=0,
                expand=True,
            )
        else:
            # 窄屏（手机竖屏）：单栏栈式导航
            if self._sub_view == "editor":
                body = ft.Container(expand=True, bgcolor=pal.bg, content=self.editor.control())
            elif self._sub_view == "detail":
                body = ft.Container(expand=True, bgcolor=pal.bg, content=self.detail.control())
            else:
                body = ft.Container(expand=True, bgcolor=pal.bg, content=self._build_list_column(pal, show_drawer_btn=True))

        self._root.content = ft.SafeArea(content=body, expand=True)
        if self.state.page:
            self.state.page.update()
