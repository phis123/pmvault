"""安全引导拦截页：
1. 一次性恢复码登入后的强制改密与新恢复码签发（不可跳过）
2. 旧版 v1 保险库单向升级并生成新一次性恢复码
3. 通用一次性恢复码展示页（醒目提示手抄记在纸上或存在手机安全处）
"""

from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from pm_core import crypto
from ..state import AppState
from ..theme import MONO_FONT, card, pill, wrap_checkbox
from .dialogs import build_qr_widget


class RecoveryDisplayScreen:
    """展示全新生成的一次性高熵恢复码（+ 二维码 + 强制勾选确认）。"""

    def __init__(
        self,
        state: AppState,
        *,
        title: str,
        subtitle: str,
        recovery_key: str,
        keyfile_note: str = "",
        on_continue: Callable[[], None],
    ) -> None:
        self.state = state
        self.title = title
        self.subtitle = subtitle
        self.recovery_key = recovery_key
        self.keyfile_note = keyfile_note
        self.on_continue = on_continue

    def build(self) -> ft.Control:
        pal = self.state.palette()
        proceed_btn = ft.FilledButton(
            "进入密码库",
            icon=ft.Icons.ARROW_FORWARD,
            disabled=True,
            on_click=lambda _e: self.on_continue(),
            height=44,
        )

        def _toggle(_e) -> None:
            proceed_btn.disabled = not bool(confirm_cb.value)
            if self.state.page:
                self.state.page.update()

        confirm_cb, confirm_row = wrap_checkbox(
            "我已将此一次性恢复码手抄在纸上或妥善存放在安全位置",
            value=False,
            on_change=_toggle,
            pal=pal,
        )

        warn_banner = card(
            ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=pal.warning, size=22),
                    ft.Text(
                        "重要：建议手抄记在纸上，或存放在手机/离线安全位置。此一次性恢复码用过一次即自动作废，关闭此页后不会再次显示。",
                        size=12,
                        color=pal.text,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            pal,
            padding=12,
            bgcolor=pal.warning_soft,
            border_color=pal.warning,
        )

        code_box = card(
            ft.Column(
                [
                    ft.Text(
                        self.recovery_key,
                        font_family=MONO_FONT,
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=pal.primary,
                        selectable=True,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Row(
                        [
                            ft.OutlinedButton(
                                "复制恢复码",
                                icon=ft.Icons.COPY,
                                on_click=lambda _e: self.state.copy_text(self.recovery_key, "一次性恢复码", is_secret=True),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            pal,
            padding=16,
            bgcolor=pal.surface_alt,
        )

        body_controls: list[ft.Control] = [
            ft.Row([ft.Icon(ft.Icons.VERIFIED_USER, color=pal.primary, size=28), ft.Text(self.title, size=20, weight=ft.FontWeight.BOLD, color=pal.text, expand=True)], spacing=10),
            ft.Text(self.subtitle, size=13, color=pal.text_muted),
            warn_banner,
            code_box,
            build_qr_widget(
                self.recovery_key,
                pal,
                size=165,
                on_copy=None,
            ),
        ]
        if self.keyfile_note:
            body_controls.append(
                card(
                    ft.Text(self.keyfile_note, size=12, color=pal.text_muted, selectable=True),
                    pal,
                    padding=10,
                    bgcolor=pal.surface_alt,
                )
            )
        body_controls.extend([confirm_row, ft.Row([proceed_btn], alignment=ft.MainAxisAlignment.END)])

        return ft.SafeArea(
            expand=True,
            content=ft.Container(
                expand=True,
                alignment=ft.Alignment(0, 0),
                padding=ft.Padding.symmetric(horizontal=20, vertical=24),
                content=ft.Column(
                    [
                        ft.Container(
                            width=520,
                            content=card(ft.Column(body_controls, spacing=14, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), pal, padding=24),
                        )
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        )


class MandatoryResetScreen:
    """一次性恢复码登入后的强制改密页（不可跳过，完成后作废旧恢复码并签发新码）。"""

    def __init__(
        self,
        state: AppState,
        *,
        on_reset_done: Callable[[str, str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self.state = state
        self.on_reset_done = on_reset_done
        self.on_cancel = on_cancel

    def build(self) -> ft.Control:
        pal = self.state.palette()
        vault = self.state.vault
        assert vault is not None

        new_pw = ft.TextField(label="设置新的主密码", password=True, can_reveal_password=True, autofocus=True)
        cfm_pw = ft.TextField(label="再次确认新主密码", password=True, can_reveal_password=True)
        use_kf, use_kf_row = wrap_checkbox(
            "同时生成/更新本机密钥文件（推荐：允许好记短密码且抗离线穷举）",
            value=True,
            pal=pal,
        )
        err_txt = ft.Text("", size=12, color=pal.danger)

        def _submit(_e=None) -> None:
            pw1 = new_pw.value or ""
            pw2 = cfm_pw.value or ""
            if pw1 != pw2:
                err_txt.value = "两次输入的主密码不一致"
                if self.state.page:
                    self.state.page.update()
                return
            pw_err = crypto.validate_master_password(pw1, has_keyfile=bool(use_kf.value))
            if pw_err:
                err_txt.value = pw_err
                if self.state.page:
                    self.state.page.update()
                return

            kf_bytes = None
            kf_note = ""
            if use_kf.value:
                kid, kf_bytes = crypto.new_keyfile()
                if self.state.current_vault_id:
                    saved_path = self.state.registry.save_keyfile_for_vault(self.state.current_vault_id, kf_bytes)
                    kf_note = f"本机密钥已隔离保存（指纹 {kid}）：{saved_path}"
            elif self.state.current_vault_id:
                self.state.registry.set_keyfile_path(self.state.current_vault_id, "")

            new_rk = vault.complete_recovery_reset(pw1, keyfile_bytes=kf_bytes)
            self.state.active_keyfile_bytes = kf_bytes
            self.on_reset_done(new_rk, kf_note)

        cfm_pw.on_submit = _submit

        return ft.SafeArea(
            expand=True,
            content=ft.Container(
                expand=True,
                alignment=ft.Alignment(0, 0),
                padding=ft.Padding.symmetric(horizontal=20, vertical=24),
                content=ft.Column(
                    [
                        ft.Container(
                            width=460,
                            content=card(
                                ft.Column(
                                    [
                                        ft.Row([pill("一次性兜底登入", pal.warning, pal.warning_soft)]),
                                        ft.Text("请立即重设主密码", size=22, weight=ft.FontWeight.BOLD, color=pal.text),
                                        ft.Text(
                                            "您刚使用一次性恢复码解开了密码库。为了保证安全，必须先重设主密码；完成后旧恢复码将立即作废，并自动为您生成全新的一次性恢复码。",
                                            size=13,
                                            color=pal.text_muted,
                                        ),
                                        new_pw,
                                        cfm_pw,
                                        use_kf_row,
                                        err_txt,
                                        ft.Row(
                                            [
                                                ft.TextButton("取消并锁定", on_click=lambda _e: self.on_cancel()),
                                                ft.FilledButton("确认重设并生成新恢复码", icon=ft.Icons.LOCK_RESET, on_click=_submit, height=44),
                                            ],
                                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        ),
                                    ],
                                    spacing=14,
                                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                ),
                                pal,
                                padding=24,
                            ),
                        )
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        )


class V1UpgradeScreen:
    """首次用新版打开旧版 v1 密码库时的自动升级页（仅单向升级为新版，备份 .v1.bak 并签发新一次性恢复码）。"""

    def __init__(
        self,
        state: AppState,
        *,
        on_upgraded: Callable[[str, str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self.state = state
        self.on_upgraded = on_upgraded
        self.on_cancel = on_cancel

    def build(self) -> ft.Control:
        pal = self.state.palette()
        vault = self.state.vault
        assert vault is not None

        use_kf, use_kf_row = wrap_checkbox(
            "升级时一并启用本机密钥文件（推荐：物理隔离存放，防止加密库泄露后被离线穷举）",
            value=False,
            pal=pal,
        )
        vname_tf = ft.TextField(label="密码库名称", value=vault.header.vault_name or "默认密码库", dense=True)
        err_txt = ft.Text("", size=12, color=pal.danger)

        def _upgrade(_e=None) -> None:
            pw = self.state.active_master_password_hint or ""
            if not pw:
                err_txt.value = "缺少当前主密码，请返回重新输入主密码解锁"
                if self.state.page:
                    self.state.page.update()
                return
            kf_bytes = None
            kf_note = ""
            if use_kf.value:
                kid, kf_bytes = crypto.new_keyfile()
                if self.state.current_vault_id:
                    saved = self.state.registry.save_keyfile_for_vault(self.state.current_vault_id, kf_bytes)
                    kf_note = f"本机密钥已隔离保存（指纹 {kid}）：{saved}"
            new_name = (vname_tf.value or "").strip() or "默认密码库"
            new_rk = vault.complete_v1_upgrade(pw, keyfile_bytes=kf_bytes, vault_name=new_name)
            if self.state.current_vault_id:
                self.state.registry.rename_vault(self.state.current_vault_id, new_name)
            self.state.active_keyfile_bytes = kf_bytes
            self.state.active_master_password_hint = None
            self.on_upgraded(new_rk, kf_note)

        return ft.SafeArea(
            expand=True,
            content=ft.Container(
                expand=True,
                alignment=ft.Alignment(0, 0),
                padding=ft.Padding.symmetric(horizontal=20, vertical=24),
                content=ft.Column(
                    [
                        ft.Container(
                            width=480,
                            content=card(
                                ft.Column(
                                    [
                                        ft.Row([pill("旧版密码库升级", pal.primary, pal.primary_soft)]),
                                        ft.Text("检测到旧版（v1）密码库", size=22, weight=ft.FontWeight.BOLD, color=pal.text),
                                        ft.Text(
                                            f"已成功解开 {len(vault.active_credentials())} 条账号数据。继续后将自动转换为新版格式（原文件会自动保留一份 .v1.bak 备份），并为您重新生成全新的一次性恢复码。",
                                            size=13,
                                            color=pal.text_muted,
                                        ),
                                        vname_tf,
                                        use_kf_row,
                                        err_txt,
                                        ft.Row(
                                            [
                                                ft.TextButton("暂不升级", on_click=lambda _e: self.on_cancel()),
                                                ft.FilledButton("升级为新版并生成恢复码", icon=ft.Icons.UPGRADE, on_click=_upgrade, height=44),
                                            ],
                                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        ),
                                    ],
                                    spacing=14,
                                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                ),
                                pal,
                                padding=24,
                            ),
                        )
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        )
