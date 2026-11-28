"""统一弹窗组件：二维码展示、密码生成器、双通道互补导出/导入、设置与安全中心。"""

from __future__ import annotations

import os
from typing import Callable, Optional

import flet as ft

from pm_core import Credential, Vault, crypto, generator, qr, sync
from ..state import AppState
from ..theme import MONO_FONT, Palette, card, pill


def show_dlg(page: ft.Page, dlg: ft.AlertDialog) -> None:
    try:
        page.show_dialog(dlg)
    except Exception:
        page.overlay.append(dlg)
        dlg.open = True
    page.update()


def close_dlg(page: ft.Page, dlg: Optional[ft.AlertDialog] = None) -> None:
    try:
        page.pop_dialog()
    except Exception:
        if dlg is not None:
            dlg.open = False
    page.update()


def build_qr_widget(text: str, pal: Palette, *, size: int = 190, on_copy: Optional[Callable[[], None]] = None) -> ft.Control:
    svg_bytes = qr.make_qr_svg_bytes(text, scale=5, border=2)
    items: list[ft.Control] = []
    if svg_bytes is not None:
        items.append(
            ft.Container(
                content=ft.Image(src=svg_bytes, width=size, height=size, fit=ft.BoxFit.CONTAIN),
                padding=10,
                bgcolor="#FFFFFF",
                border_radius=12,
                border=ft.Border.all(1, pal.border),
            )
        )
    else:
        items.append(
            ft.Container(
                content=ft.Text("当前环境未启用二维码渲染，请直接复制下方文本", size=12, color=pal.text_muted),
                padding=12,
                bgcolor=pal.surface_alt,
                border_radius=8,
            )
        )
    if on_copy is not None:
        items.append(
            ft.TextButton("复制二维码文本", icon=ft.Icons.COPY, on_click=lambda _e: on_copy())
        )
    return ft.Column(items, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)


# ------------------------------------------------------------------ 密码生成器弹窗

def open_generator_dialog(state: AppState, on_use: Optional[Callable[[str], None]] = None) -> None:
    page = state.page
    if page is None:
        return
    pal = state.palette()
    opts = dict(state.settings.get("generator") or generator.DEFAULT_OPTIONS)

    pwd_text = ft.Text(
        generator.generate_password(opts),
        font_family=MONO_FONT,
        size=16,
        weight=ft.FontWeight.W_600,
        selectable=True,
        color=pal.text,
    )
    strength_lbl = ft.Text("", size=12)

    def _refresh_strength() -> None:
        st = crypto.password_strength(pwd_text.value or "")
        colors = [pal.danger, pal.danger, pal.warning, pal.success, pal.success]
        strength_lbl.value = f"强度：{st['label']}（约 {st['entropy']} bit）"
        strength_lbl.color = colors[st["score"]]

    _refresh_strength()

    length_label = ft.Text(f"长度：{int(opts.get('length', 20))} 位", size=13, color=pal.text)

    def _regen(_e=None) -> None:
        opts["length"] = int(length_slider.value)
        opts["lower"] = bool(cb_lower.value)
        opts["upper"] = bool(cb_upper.value)
        opts["digits"] = bool(cb_digits.value)
        opts["symbols"] = bool(cb_symbols.value)
        opts["exclude_confusing"] = bool(cb_confuse.value)
        length_label.value = f"长度：{opts['length']} 位"
        try:
            pwd_text.value = generator.generate_password(opts)
        except Exception:
            cb_lower.value = True
            opts["lower"] = True
            pwd_text.value = generator.generate_password(opts)
        _refresh_strength()
        state.settings["generator"] = dict(opts)
        state.save_settings()
        page.update()

    length_slider = ft.Slider(
        min=8, max=40, divisions=32, value=int(opts.get("length", 20)), on_change=_regen
    )
    cb_lower = ft.Checkbox(label="小写字母 (a-z)", value=bool(opts.get("lower", True)), on_change=_regen)
    cb_upper = ft.Checkbox(label="大写字母 (A-Z)", value=bool(opts.get("upper", True)), on_change=_regen)
    cb_digits = ft.Checkbox(label="数字 (0-9)", value=bool(opts.get("digits", True)), on_change=_regen)
    cb_symbols = ft.Checkbox(label="符号 (!@#...)", value=bool(opts.get("symbols", True)), on_change=_regen)
    cb_confuse = ft.Checkbox(label="排除易混字符 (0/O/1/l/I)", value=bool(opts.get("exclude_confusing", True)), on_change=_regen)

    actions: list[ft.Control] = [
        ft.TextButton("关闭", on_click=lambda _e: close_dlg(page, dlg)),
        ft.OutlinedButton("复制", icon=ft.Icons.COPY, on_click=lambda _e: state.copy_text(pwd_text.value or "", "生成密码", is_secret=True)),
    ]
    if on_use is not None:
        def _apply(_e) -> None:
            val = pwd_text.value or ""
            close_dlg(page, dlg)
            on_use(val)
        actions.append(ft.FilledButton("填入使用", icon=ft.Icons.CHECK, on_click=_apply))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("密码生成器", weight=ft.FontWeight.W_600),
        content=ft.Container(
            width=400,
            content=ft.Column(
                [
                    card(
                        ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Container(content=pwd_text, expand=True),
                                        ft.IconButton(ft.Icons.REFRESH, tooltip="重新生成", on_click=_regen),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                strength_lbl,
                            ],
                            spacing=6,
                        ),
                        pal,
                        padding=12,
                        bgcolor=pal.surface_alt,
                    ),
                    length_label,
                    length_slider,
                    ft.Row([cb_lower, cb_upper], wrap=True),
                    ft.Row([cb_digits, cb_symbols], wrap=True),
                    cb_confuse,
                ],
                spacing=6,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=actions,
    )
    show_dlg(page, dlg)


# ------------------------------------------------------------------ 双通道互补导出弹窗

def open_dual_export_dialog(state: AppState) -> None:
    page = state.page
    vault = state.vault
    if page is None or vault is None:
        return
    pal = state.palette()

    cb_keyfile = ft.Checkbox(
        label="二维码中附带本机密钥（方便目标设备一步绑定）",
        value=bool(vault.header.keyfile_required and state.active_keyfile_bytes),
        disabled=not bool(vault.header.keyfile_required and state.active_keyfile_bytes),
    )
    cb_recovery = ft.Checkbox(
        label="二维码中附带当前一次性恢复码（仅当面扫码，切勿截图外传）",
        value=bool(vault.last_recovery_key),
        disabled=not bool(vault.last_recovery_key),
    )
    mp_input = ft.TextField(
        label="可选：在二维码中附带登录主密码（留空则不附带）",
        password=True,
        can_reveal_password=True,
        dense=True,
        hint_text="仅限两台设备当面扫码迁移时填写",
    )

    default_dir = os.path.join(state.data_dir, "exports")
    os.makedirs(default_dir, exist_ok=True)
    default_path = os.path.join(default_dir, sync.default_filename(vault.header.revision, state.device_name, vault.header.vault_name))
    path_field = ft.TextField(label="加密同步文件保存路径 (.pmvsync)", value=default_path, dense=True)

    result_box = ft.Column([], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
    status_txt = ft.Text("", size=12, color=pal.text_muted)

    def _generate(_e=None) -> None:
        pkg = sync.pack_dual_channel(
            vault.credentials,
            revision=vault.header.revision,
            device=state.device_name,
            vault_name=vault.header.vault_name,
            master_password=(mp_input.value or "").strip() or None,
            keyfile_bytes=state.active_keyfile_bytes if cb_keyfile.value else None,
            recovery_key=vault.last_recovery_key if cb_recovery.value else None,
        )
        target = (path_field.value or "").strip() or default_path
        try:
            saved = sync.save_dual_channel_file(target, pkg["file_text"])
        except Exception as exc:
            status_txt.value = f"保存失败：{exc}"
            status_txt.color = pal.danger
            page.update()
            return

        status_txt.value = f"已生成并保存：{saved}（配对号 {pkg['bundle_id']}，缺二维码无法解开）"
        status_txt.color = pal.success
        result_box.controls = [
            ft.Row(
                [
                    pill(f"配对号 {pkg['bundle_id']}", pal.primary, pal.primary_soft),
                    pill(f"{len(vault.credentials)} 条记录", pal.text_muted, pal.surface_alt),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
            ),
            build_qr_widget(
                pkg["qr_payload"],
                pal,
                size=210,
                on_copy=lambda p=pkg["qr_payload"]: state.copy_text(p, "互补二维码文本", is_secret=True),
            ),
            ft.Text(
                "请将加密文件发到目标设备，并当面扫描此二维码导入。文件与二维码缺一不可。",
                size=12,
                color=pal.warning,
                text_align=ft.TextAlign.CENTER,
            ),
        ]
        page.update()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("双通道互补导出（文件 + 二维码）", weight=ft.FontWeight.W_600),
        content=ft.Container(
            width=460,
            content=ft.Column(
                [
                    ft.Text(
                        "加密文件只存密文，传输密钥存在配套二维码中，缺一不可。",
                        size=12,
                        color=pal.text_muted,
                    ),
                    path_field,
                    cb_keyfile,
                    cb_recovery,
                    mp_input,
                    ft.FilledButton("生成加密文件与互补二维码", icon=ft.Icons.QR_CODE_2, on_click=_generate),
                    status_txt,
                    result_box,
                ],
                spacing=10,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=[ft.TextButton("完成", on_click=lambda _e: close_dlg(page, dlg))],
    )
    show_dlg(page, dlg)


# ------------------------------------------------------------------ 智能导入弹窗（支持 PM2 双通道 / 旧版 PM1 / 外部 .pmvault）

def open_import_dialog(
    state: AppState,
    *,
    allow_merge_current: bool = True,
    on_done: Optional[Callable[[str], None]] = None,
) -> None:
    page = state.page
    if page is None:
        return
    pal = state.palette()

    file_path = ft.TextField(
        label="同步文件或保险库路径 (.pmvsync / .pmvault)",
        hint_text="输入文件路径，支持新版 PM2、旧版 PM1 与旧版 .pmvault",
        dense=True,
    )
    qr_or_pass = ft.TextField(
        label="配套互补二维码文本 (PMQR2:...) 或 旧版同步口令",
        hint_text="PM2 双通道文件填二维码文本；旧版 PM1 文件填同步口令；.pmvault 可留空",
        multiline=True,
        min_lines=2,
        max_lines=4,
        dense=True,
    )
    new_vault_name = ft.TextField(label="导入为新库时的名称（可选）", dense=True)
    new_vault_pw = ft.TextField(
        label="新建为独立库时的主密码（若二维码未自带主密码）",
        password=True,
        can_reveal_password=True,
        dense=True,
    )
    mode_radio = ft.RadioGroup(
        value="merge" if (allow_merge_current and state.vault is not None) else "new_vault",
        content=ft.Row(
            [
                ft.Radio(value="merge", label="合并到当前库", disabled=not (allow_merge_current and state.vault is not None)),
                ft.Radio(value="new_vault", label="导入为独立新库"),
            ]
        ),
    )
    err_txt = ft.Text("", size=12, color=pal.danger)

    def _do_import(_e) -> None:
        p = (file_path.value or "").strip().strip('"')
        if not p or not os.path.exists(p):
            err_txt.value = "找不到指定文件，请检查路径"
            page.update()
            return

        # 情况 1：直接挂载/导入 .pmvault 文件（兼容 v1 与 v2）
        if p.lower().endswith(".pmvault"):
            try:
                rec = state.registry.register_existing_file(
                    p, name=(new_vault_name.value or "").strip() or None, copy_into_managed=True
                )
                state.select_vault_id(rec.id)
                close_dlg(page, dlg)
                state.toast(f"已导入密码库「{rec.name}」，请输入主密码解锁")
                if on_done:
                    on_done("vault_added")
            except Exception as exc:
                err_txt.value = f"导入 .pmvault 失败：{exc}"
                page.update()
            return

        # 情况 2：.pmvsync 同步文件（自动识别 PM2 双通道 / PM1 旧版兼容）
        lines = sync.load_lines(p)
        fmt = sync.detect_format(lines)
        secret_in = (qr_or_pass.value or "").strip()

        try:
            if fmt == sync.DUAL_MAGIC:
                data = sync.unpack_dual_channel(lines, secret_in)
            elif fmt == sync.LEGACY_MAGIC:
                if not secret_in:
                    raise sync.SyncError("检测到旧版 PM1 同步文件，请输入原同步口令")
                data = sync.unpack(lines, secret_in)
            else:
                raise sync.SyncError("无法识别的文件格式（请提供 .pmvsync 或 .pmvault）")
        except Exception as exc:
            err_txt.value = str(exc)
            page.update()
            return

        creds: list[Credential] = data["credentials"]
        if mode_radio.value == "merge" and state.vault is not None:
            existing_ids = {c.id for c in state.vault.credentials}
            added, updated = 0, 0
            for c in creds:
                if c.id in existing_ids:
                    state.vault.update(c)
                    updated += 1
                else:
                    state.vault.add(c)
                    added += 1
            close_dlg(page, dlg)
            state.toast(f"导入完成：新增 {added} 条，更新 {updated} 条（已转为新版格式）")
            if on_done:
                on_done("merged")
            return

        # 导入为独立新库
        vname = (new_vault_name.value or "").strip() or data.get("vault_name") or "导入的密码库"
        mp = (new_vault_pw.value or "").strip() or data.get("master_password") or ""
        kf_bytes = data.get("keyfile_bytes")
        pw_err = crypto.validate_master_password(mp, has_keyfile=bool(kf_bytes))
        if pw_err:
            err_txt.value = f"请为新导入的密码库设置主密码：{pw_err}"
            page.update()
            return
        try:
            rec, new_v, _ = state.registry.create_vault(
                vname, mp, device=state.device_name, use_keyfile=bool(kf_bytes), keyfile_bytes=kf_bytes
            )
            for c in creds:
                new_v.add(c)
            new_rk = new_v.last_recovery_key
            new_v.lock()
            state.select_vault_id(rec.id)
            close_dlg(page, dlg)
            state.toast(f"已创建新库「{rec.name}」（{len(creds)} 条），新一次性恢复码：{new_rk}")
            if on_done:
                on_done("vault_added")
        except Exception as exc:
            err_txt.value = f"创建新库失败：{exc}"
            page.update()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("导入密码库 / 同步文件", weight=ft.FontWeight.W_600),
        content=ft.Container(
            width=460,
            content=ft.Column(
                [
                    ft.Text("自动兼容新版双通道 .pmvsync (PM2)、旧版口令 .pmvsync (PM1) 及旧版 .pmvault。", size=12, color=pal.text_muted),
                    file_path,
                    qr_or_pass,
                    mode_radio,
                    new_vault_name,
                    new_vault_pw,
                    err_txt,
                ],
                spacing=10,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=[
            ft.TextButton("取消", on_click=lambda _e: close_dlg(page, dlg)),
            ft.FilledButton("开始导入", icon=ft.Icons.DOWNLOAD, on_click=_do_import),
        ],
    )
    show_dlg(page, dlg)


# ------------------------------------------------------------------ 设置与安全中心弹窗

def open_settings_dialog(
    state: AppState,
    *,
    on_theme_or_setting_changed: Callable[[], None],
    on_show_new_recovery: Callable[[str, str], None],
) -> None:
    page = state.page
    vault = state.vault
    if page is None or vault is None:
        return
    pal = state.palette()

    theme_dd = ft.Dropdown(
        label="外观主题",
        value=str(state.settings.get("theme_mode", "system")),
        options=[
            ft.dropdown.Option("system", "跟随系统"),
            ft.dropdown.Option("light", "浅色模式"),
            ft.dropdown.Option("dark", "深色模式"),
        ],
        dense=True,
    )
    autolock_tf = ft.TextField(label="无操作自动锁定（分钟，0 为关闭）", value=str(state.settings.get("auto_lock_minutes", 5)), dense=True)
    clip_tf = ft.TextField(label="剪贴板自动清空（秒）", value=str(state.settings.get("clipboard_clear_seconds", 20)), dense=True)
    old_tf = ft.TextField(label="密码过期提醒阈值（天）", value=str(state.settings.get("old_password_days", 180)), dense=True)
    vname_tf = ft.TextField(label="当前密码库名称", value=vault.header.vault_name, dense=True)

    def _save_basic(_e) -> None:
        try:
            state.settings["theme_mode"] = theme_dd.value or "system"
            state.settings["auto_lock_minutes"] = max(0, int(autolock_tf.value or 5))
            state.settings["clipboard_clear_seconds"] = max(0, int(clip_tf.value or 20))
            state.settings["old_password_days"] = max(1, int(old_tf.value or 180))
            state.save_settings()
            new_name = (vname_tf.value or "").strip()
            if new_name and new_name != vault.header.vault_name:
                vault.header.vault_name = new_name
                vault.save()
                if state.current_vault_id:
                    state.registry.rename_vault(state.current_vault_id, new_name)
            state.apply_theme()
            close_dlg(page, dlg)
            state.toast("设置已保存")
            on_theme_or_setting_changed()
        except Exception as exc:
            state.toast(f"保存失败：{exc}", error=True)

    def _export_keyfile(_e) -> None:
        if not state.active_keyfile_bytes:
            state.toast("当前库未启用本机密钥文件", error=True)
            return
        qr_text = crypto.encode_keyfile_qr(state.active_keyfile_bytes)
        rec = state.registry.get(state.current_vault_id)
        kpath = rec.keyfile_path if rec else ""
        close_dlg(page, dlg)
        kdlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("本机密钥备份（请勿与 .pmvault 存同一网盘）", weight=ft.FontWeight.W_600),
            content=ft.Container(
                width=420,
                content=ft.Column(
                    [
                        ft.Text(f"指纹编号：{vault.header.keyfile_id}", size=13, weight=ft.FontWeight.W_600),
                        ft.Text(f"本机存放路径：{kpath or '(未记录)'}", size=12, color=pal.text_muted, selectable=True),
                        build_qr_widget(
                            qr_text,
                            pal,
                            size=200,
                            on_copy=lambda: state.copy_text(qr_text, "本机密钥二维码文本", is_secret=True),
                        ),
                    ],
                    spacing=10,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
            actions=[ft.FilledButton("我已了解", on_click=lambda _ev: close_dlg(page, kdlg))],
        )
        show_dlg(page, kdlg)

    def _rotate_recovery(_e) -> None:
        new_rk = vault.rotate_recovery_key()
        close_dlg(page, dlg)
        on_show_new_recovery("已重新生成一次性恢复码", new_rk)

    def _open_change_master(_e) -> None:
        close_dlg(page, dlg)
        open_change_master_dialog(state, on_show_new_recovery=on_show_new_recovery)

    kf_status = f"已启用（指纹 {vault.header.keyfile_id}）" if vault.header.keyfile_required else "未启用"
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("设置与安全", weight=ft.FontWeight.W_600),
        content=ft.Container(
            width=440,
            content=ft.Column(
                [
                    vname_tf,
                    theme_dd,
                    autolock_tf,
                    clip_tf,
                    old_tf,
                    ft.Divider(color=pal.border),
                    ft.Text(f"本机密钥文件：{kf_status}", size=13, color=pal.text_muted),
                    ft.Row(
                        [
                            ft.OutlinedButton("修改主密码 / 本机密钥", icon=ft.Icons.PASSWORD, on_click=_open_change_master),
                            ft.OutlinedButton("轮换一次性恢复码", icon=ft.Icons.KEY, on_click=_rotate_recovery),
                        ],
                        wrap=True,
                        spacing=8,
                    ),
                    ft.OutlinedButton(
                        "查看/备份本机密钥二维码",
                        icon=ft.Icons.QR_CODE,
                        on_click=_export_keyfile,
                        disabled=not bool(vault.header.keyfile_required and state.active_keyfile_bytes),
                    ),
                ],
                spacing=10,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=[
            ft.TextButton("取消", on_click=lambda _e: close_dlg(page, dlg)),
            ft.FilledButton("保存设置", on_click=_save_basic),
        ],
    )
    show_dlg(page, dlg)


def open_change_master_dialog(state: AppState, *, on_show_new_recovery: Callable[[str, str], None]) -> None:
    page = state.page
    vault = state.vault
    if page is None or vault is None:
        return
    pal = state.palette()

    old_pw = ft.TextField(label="原主密码", password=True, can_reveal_password=True, dense=True)
    new_pw = ft.TextField(label="新主密码", password=True, can_reveal_password=True, dense=True)
    cfm_pw = ft.TextField(label="确认新主密码", password=True, can_reveal_password=True, dense=True)
    use_kf = ft.Checkbox(label="启用本机密钥文件防离线穷举", value=vault.header.keyfile_required)
    err = ft.Text("", size=12, color=pal.danger)

    def _submit(_e) -> None:
        if (new_pw.value or "") != (cfm_pw.value or ""):
            err.value = "两次输入的新主密码不一致"
            page.update()
            return
        pw_err = crypto.validate_master_password(new_pw.value or "", has_keyfile=bool(use_kf.value))
        if pw_err:
            err.value = pw_err
            page.update()
            return
        try:
            target_kf = None
            if use_kf.value:
                if state.active_keyfile_bytes:
                    target_kf = state.active_keyfile_bytes
                else:
                    _, target_kf = crypto.new_keyfile()
                    if state.current_vault_id:
                        state.registry.save_keyfile_for_vault(state.current_vault_id, target_kf)
            new_rk = vault.change_master_password(
                old_pw.value or "",
                new_pw.value or "",
                old_keyfile_bytes=state.active_keyfile_bytes,
                new_keyfile_bytes=target_kf,
                keep_existing_keyfile=False,
            )
            state.active_keyfile_bytes = target_kf
            if not use_kf.value and state.current_vault_id:
                state.registry.set_keyfile_path(state.current_vault_id, "")
            close_dlg(page, dlg)
            on_show_new_recovery("主密码已更新，请保存新的一次性恢复码", new_rk)
        except Exception as exc:
            err.value = str(exc)
            page.update()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("修改主密码与本机密钥", weight=ft.FontWeight.W_600),
        content=ft.Container(
            width=400,
            content=ft.Column([old_pw, new_pw, cfm_pw, use_kf, err], spacing=10, tight=True),
        ),
        actions=[
            ft.TextButton("取消", on_click=lambda _e: close_dlg(page, dlg)),
            ft.FilledButton("确认修改", on_click=_submit),
        ],
    )
    show_dlg(page, dlg)
