"""登录与多密码库选择页（支持日常主密码解锁、一次性恢复码兜底登入、新建库、导入库）。"""

from __future__ import annotations

import os
from typing import Callable, Optional

import flet as ft

from pm_core import (
    KeyfileMismatchError,
    KeyfileRequiredError,
    Vault,
    WrongPasswordError,
    crypto,
)
from ..state import APP_TITLE, AppState
from ..theme import card, pill, wrap_checkbox
from .dialogs import close_dlg, open_import_dialog, show_dlg


class LoginScreen:
    def __init__(
        self,
        state: AppState,
        *,
        on_unlocked: Callable[[], None],
        on_need_v1_upgrade: Callable[[], None],
        on_need_recovery_reset: Callable[[], None],
        on_vault_created: Callable[[str, str], None],
    ) -> None:
        self.state = state
        self.on_unlocked = on_unlocked
        self.on_need_v1_upgrade = on_need_v1_upgrade
        self.on_need_recovery_reset = on_need_recovery_reset
        self.on_vault_created = on_vault_created
        self._recovery_mode = False
        self._root = ft.Container(expand=True)

    def build(self) -> ft.Control:
        self._render()
        return self._root

    def _render(self) -> None:
        pal = self.state.palette()
        self.state.registry.reload()
        vaults = self.state.registry.list_vaults()
        selected_id = self.state.ensure_selected_vault_id()
        current_info = next((v for v in vaults if v["id"] == selected_id), None)

        header = ft.Column(
            [
                ft.Container(
                    content=ft.Icon(ft.Icons.SHIELD_OUTLINED, size=34, color="#FFFFFF"),
                    width=60,
                    height=60,
                    border_radius=16,
                    bgcolor=pal.primary,
                    alignment=ft.Alignment(0, 0),
                ),
                ft.Text(APP_TITLE, size=24, weight=ft.FontWeight.BOLD, color=pal.text),
                ft.Text("离线加密密码库 · 双端统一 · 零云端依赖", size=13, color=pal.text_muted),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        )

        if not vaults:
            main_card = self._build_create_first_card()
        else:
            main_card = self._build_unlock_card(vaults, current_info)

        self._root.content = ft.SafeArea(
            expand=True,
            content=ft.Container(
                expand=True,
                alignment=ft.Alignment(0, 0),
                padding=ft.Padding.symmetric(horizontal=20, vertical=24),
                content=ft.Column(
                    [
                        header,
                        ft.Container(height=12),
                        ft.Container(width=460, content=main_card),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    scroll=ft.ScrollMode.AUTO,
                    spacing=8,
                ),
            ),
        )
        if self.state.page:
            self.state.page.update()

    # ------------------------------------------------------------ 首次创建向导
    def _build_create_first_card(self) -> ft.Control:
        pal = self.state.palette()
        name_tf = ft.TextField(label="密码库名称", value="我的密码库", dense=True)
        pw_tf = ft.TextField(label="设置主密码", password=True, can_reveal_password=True, dense=True)
        cfm_tf = ft.TextField(label="确认主密码", password=True, can_reveal_password=True, dense=True)
        use_kf, use_kf_row = wrap_checkbox("启用本机密钥文件（推荐：好记短密码也能抗离线穷举）", value=True, pal=pal)
        err = ft.Text("", size=12, color=pal.danger)

        def _create(_e=None) -> None:
            pw1, pw2 = pw_tf.value or "", cfm_tf.value or ""
            if pw1 != pw2:
                err.value = "两次输入的主密码不一致"
                self.state.page.update()
                return
            pw_err = crypto.validate_master_password(pw1, has_keyfile=bool(use_kf.value))
            if pw_err:
                err.value = pw_err
                self.state.page.update()
                return
            try:
                rec, vault, kf_path = self.state.registry.create_vault(
                    name_tf.value or "我的密码库",
                    pw1,
                    device=self.state.device_name,
                    use_keyfile=bool(use_kf.value),
                )
                self.state.select_vault_id(rec.id)
                self.state.vault = vault
                self.state.active_keyfile_bytes = self.state.load_keyfile_for_current()
                kf_note = f"本机密钥已隔离保存（指纹 {vault.header.keyfile_id}）：{kf_path}" if kf_path else ""
                self.on_vault_created(vault.last_recovery_key or "", kf_note)
            except Exception as exc:
                err.value = str(exc)
                self.state.page.update()

        cfm_tf.on_submit = _create
        return card(
            ft.Column(
                [
                    ft.Text("创建你的第一个密码库", size=18, weight=ft.FontWeight.W_600, color=pal.text),
                    name_tf,
                    pw_tf,
                    cfm_tf,
                    use_kf_row,
                    err,
                    ft.FilledButton("创建并生成一次性恢复码", icon=ft.Icons.ADD_MODERATOR, on_click=_create, height=46),
                    ft.Row(
                        [
                            ft.TextButton(
                                "导入已有密码库 / 同步文件",
                                icon=ft.Icons.DOWNLOAD,
                                on_click=lambda _e: open_import_dialog(
                                    self.state, allow_merge_current=False, on_done=lambda _k: self._render()
                                ),
                            )
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=14,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            pal,
            padding=24,
        )

    # ------------------------------------------------------------ 多库选择 + 解锁卡片
    def _build_unlock_card(self, vaults: list[dict], current: Optional[dict]) -> ft.Control:
        pal = self.state.palette()
        assert current is not None

        def _on_vault_change(e) -> None:
            if e.control.value:
                self.state.select_vault_id(e.control.value)
                self._recovery_mode = False
                self._render()

        dd = ft.Dropdown(
            label="选择要登录的密码库",
            value=current["id"],
            options=[
                ft.dropdown.Option(
                    v["id"],
                    f"{v['name']}" + (" [旧版 v1]" if v["is_v1"] else "") + (" · 本机密钥" if v["keyfile_required"] else ""),
                )
                for v in vaults
            ],
            on_select=_on_vault_change,
            dense=True,
            expand=True,
        )

        badges: list[ft.Control] = []
        if current["is_v1"]:
            badges.append(pill("旧版 v1 · 解锁后自动升级", pal.warning, pal.warning_soft))
        else:
            badges.append(pill(f"v{current['version']} · 修订 #{current['revision']}", pal.text_muted, pal.surface_alt))
        if current["keyfile_required"]:
            if current["keyfile_exists"]:
                badges.append(pill(f"本机密钥就绪 ({current['keyfile_id']})", pal.success, pal.success_soft))
            else:
                badges.append(pill(f"缺少本机密钥 ({current['keyfile_id']})", pal.danger, pal.danger_soft))

        err_txt = ft.Text("", size=12, color=pal.danger)

        # 若需本机密钥但本地缺失，允许粘贴二维码文本或指定文件路径绑定
        kf_input = ft.TextField(
            label=f"导入本机密钥（编号 {current['keyfile_id']}）：粘贴 PMKEY1:... 或文件路径",
            dense=True,
            visible=bool(not self._recovery_mode and current["keyfile_required"] and not current["keyfile_exists"]),
        )

        if not self._recovery_mode:
            secret_tf = ft.TextField(
                label="主密码",
                password=True,
                can_reveal_password=True,
                autofocus=True,
                dense=True,
            )
            btn_text = "解锁密码库"
            btn_icon = ft.Icons.LOCK_OPEN
            switch_text = "忘记主密码？使用一次性恢复码兜底登入"
        else:
            secret_tf = ft.TextField(
                label="一次性恢复码（XXXX-XXXX-XXXX-XXXX-XXXX-XXXX）",
                hint_text="登入后将强制修改主密码并签发全新恢复码",
                autofocus=True,
                dense=True,
            )
            btn_text = "验证恢复码并重设主密码"
            btn_icon = ft.Icons.KEY
            switch_text = "返回常规主密码登录"

        def _unlock(_e=None) -> None:
            wait = self.state.throttle.cooldown_remaining()
            if wait > 0:
                err_txt.value = f"尝试次数过多，请等待 {int(wait) + 1} 秒后再试"
                self.state.page.update()
                return

            val = (secret_tf.value or "").strip() if self._recovery_mode else (secret_tf.value or "")
            if not val:
                err_txt.value = "请输入一次性恢复码" if self._recovery_mode else "请输入主密码"
                self.state.page.update()
                return

            if self._recovery_mode:
                try:
                    vault = Vault.unlock_with_recovery_key(current["path"], val)
                except Exception as exc:
                    self.state.throttle.register_failure()
                    err_txt.value = str(exc)
                    self.state.page.update()
                    return
                self.state.throttle.register_success()
                self.state.vault = vault
                self.state.active_keyfile_bytes = None
                self.state.touch_activity()
                self.on_need_recovery_reset()
                return

            # 常规主密码解锁（如启用本机密钥则加载）
            kf_bytes: Optional[bytes] = None
            if current["keyfile_required"]:
                try:
                    if current["keyfile_exists"]:
                        _, kf_bytes = crypto.load_keyfile(current["keyfile_path"])
                    else:
                        raw_in = (kf_input.value or "").strip().strip('"')
                        if not raw_in:
                            raise KeyfileRequiredError(current["keyfile_id"])
                        if raw_in.startswith("PMKEY1:"):
                            _, kf_bytes = crypto.decode_keyfile_qr(raw_in)
                        else:
                            _, kf_bytes = crypto.load_keyfile(raw_in)
                        # 验证通过后顺便隔离保存到本机
                        if crypto.keyfile_fingerprint(kf_bytes) != current["keyfile_id"]:
                            raise KeyfileMismatchError(current["keyfile_id"], crypto.keyfile_fingerprint(kf_bytes))
                        self.state.registry.save_keyfile_for_vault(current["id"], kf_bytes)
                except Exception as exc:
                    err_txt.value = str(exc)
                    self.state.page.update()
                    return

            try:
                vault = Vault.unlock(current["path"], val, keyfile_bytes=kf_bytes)
            except WrongPasswordError as exc:
                w = self.state.throttle.register_failure()
                err_txt.value = f"{exc}" + (f"（已触发退避，请 {int(w)} 秒后重试）" if w > 0 else "")
                self.state.page.update()
                return
            except Exception as exc:
                err_txt.value = str(exc)
                self.state.page.update()
                return

            self.state.throttle.register_success()
            self.state.vault = vault
            self.state.active_keyfile_bytes = kf_bytes
            self.state.touch_activity()
            if vault.migrated_from_v1:
                self.state.active_master_password_hint = val
                self.on_need_v1_upgrade()
            else:
                self.on_unlocked()

        secret_tf.on_submit = _unlock

        def _toggle_mode(_e) -> None:
            self._recovery_mode = not self._recovery_mode
            self._render()

        def _open_new_vault_dlg(_e) -> None:
            self._open_create_vault_dialog()

        return card(
            ft.Column(
                [
                    ft.Row(
                        [
                            dd,
                            ft.IconButton(ft.Icons.ADD, tooltip="新建密码库", on_click=_open_new_vault_dlg),
                            ft.IconButton(
                                ft.Icons.DOWNLOAD,
                                tooltip="导入密码库 / 双通道同步",
                                on_click=lambda _e: open_import_dialog(
                                    self.state, allow_merge_current=False, on_done=lambda _k: self._render()
                                ),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(badges, wrap=True, spacing=6),
                    kf_input,
                    secret_tf,
                    err_txt,
                    ft.FilledButton(btn_text, icon=btn_icon, on_click=_unlock, height=46),
                    ft.Row(
                        [ft.TextButton(switch_text, on_click=_toggle_mode)],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=14,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            pal,
            padding=24,
        )

    def _open_create_vault_dialog(self) -> None:
        page = self.state.page
        if page is None:
            return
        pal = self.state.palette()
        name_tf = ft.TextField(label="新密码库名称", hint_text="如：工作库、个人日常库", dense=True)
        pw_tf = ft.TextField(label="主密码", password=True, can_reveal_password=True, dense=True)
        cfm_tf = ft.TextField(label="确认主密码", password=True, can_reveal_password=True, dense=True)
        use_kf, use_kf_row = wrap_checkbox("启用本机密钥文件防离线穷举（推荐）", value=True, pal=pal)
        err = ft.Text("", size=12, color=pal.danger)

        def _submit(_e=None) -> None:
            if (pw_tf.value or "") != (cfm_tf.value or ""):
                err.value = "两次输入的主密码不一致"
                page.update()
                return
            pw_err = crypto.validate_master_password(pw_tf.value or "", has_keyfile=bool(use_kf.value))
            if pw_err:
                err.value = pw_err
                page.update()
                return
            try:
                rec, vault, kf_path = self.state.registry.create_vault(
                    name_tf.value or "新密码库",
                    pw_tf.value or "",
                    device=self.state.device_name,
                    use_keyfile=bool(use_kf.value),
                )
                close_dlg(page, dlg)
                self.state.select_vault_id(rec.id)
                self.state.vault = vault
                self.state.active_keyfile_bytes = self.state.load_keyfile_for_current()
                kf_note = f"本机密钥已隔离保存（指纹 {vault.header.keyfile_id}）：{kf_path}" if kf_path else ""
                self.on_vault_created(vault.last_recovery_key or "", kf_note)
            except Exception as exc:
                err.value = str(exc)
                page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("新建独立密码库", weight=ft.FontWeight.W_600),
            content=ft.Container(
                width=400,
                content=ft.Column([name_tf, pw_tf, cfm_tf, use_kf_row, err], spacing=10, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _e: close_dlg(page, dlg)),
                ft.FilledButton("创建", on_click=_submit),
            ],
        )
        show_dlg(page, dlg)
