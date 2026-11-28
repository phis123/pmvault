"""现代分块卡片详情页：
按业务分块展示 9 类账号信息，空区块自动隐藏保持视觉极简；
所有局部交互（小眼睛显隐密码/密保/密钥、收藏、历史展开、划掉备用恢复码）
均采用「原地控件更新」，绝不重建滚动容器，彻底避免手机端点击后跳回页面顶部。
"""

from __future__ import annotations

from typing import Callable, Optional, Set

import flet as ft

from pm_core import Credential, crypto, totp
from ..state import AppState
from ..theme import MONO_FONT, Palette, card, pill, section_title


class DetailView:
    def __init__(
        self,
        state: AppState,
        *,
        on_edit: Callable[[Credential], None],
        on_changed: Callable[[], None],
        on_back: Optional[Callable[[], None]] = None,
    ) -> None:
        self.state = state
        self.on_edit = on_edit
        self.on_changed = on_changed
        self.on_back = on_back

        self._cred: Optional[Credential] = None
        self._show_password = False
        self._revealed_keys: Set[str] = set()
        self._show_history = False
        self._show_back = False

        # 实时 TOTP 控件引用，供外部每秒刷新
        self._totp_code_txt: Optional[ft.Text] = None
        self._totp_rem_txt: Optional[ft.Text] = None
        self._totp_bar: Optional[ft.ProgressBar] = None

        self._scroll_col = ft.Column([], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
        self._container = ft.Container(expand=True)

    def control(self) -> ft.Control:
        return self._container

    def set_credential(self, cred: Optional[Credential], *, show_back: bool = False) -> None:
        if cred is None or (self._cred and cred.id != self._cred.id):
            self._show_password = False
            self._revealed_keys.clear()
            self._show_history = False
        self._cred = cred
        self._show_back = show_back
        self.render()

    def tick_totp(self) -> None:
        """由主循环每秒调用，实时更新当前详情页的 2FA 验证码与倒计时条。"""
        if self._cred is None or not (self._cred.totp_secret or "").strip():
            return
        if self._totp_code_txt is None or self._totp_rem_txt is None or self._totp_bar is None:
            return
        try:
            code, rem, period = totp.generate_totp(self._cred.totp_secret)
            pal = self.state.palette()
            self._totp_code_txt.value = totp.format_code(code)
            self._totp_rem_txt.value = f"{rem}s"
            self._totp_bar.value = max(0.0, min(1.0, rem / float(period)))
            self._totp_bar.color = pal.warning if rem <= 5 else pal.primary
            if self.state.page:
                self.state.page.update()
        except Exception:
            pass

    def render(self) -> None:
        pal = self.state.palette()
        self._totp_code_txt = None
        self._totp_rem_txt = None
        self._totp_bar = None

        cred = self._cred
        vault = self.state.vault
        if cred is None or vault is None:
            self._container.content = ft.Container(
                expand=True,
                alignment=ft.Alignment(0, 0),
                padding=20,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.KEY_OFF_OUTLINED, size=48, color=pal.text_faint),
                        ft.Text("选择左侧条目查看详情，或点击「+」新增账号", size=14, color=pal.text_muted, text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=10,
                ),
            )
            return

        blocks: list[ft.Control] = []

        # 1. 顶部标题栏（手机窄屏拆为两行：顶栏操作 + 满宽标题行）
        avatar = ft.Container(
            content=ft.Text((cred.name[:1] or "?").upper(), size=18, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
            width=42,
            height=42,
            border_radius=12,
            bgcolor=pal.primary,
            alignment=ft.Alignment(0, 0),
        )
        title_col = ft.Column(
            [
                ft.Text(
                    cred.name,
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=pal.text,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    cred.url or cred.username or "未填写网址",
                    size=12,
                    color=pal.text_muted,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
            spacing=2,
            expand=True,
        )

        fav_btn = ft.IconButton(
            ft.Icons.STAR if cred.favorite else ft.Icons.STAR_BORDER,
            icon_color=pal.warning if cred.favorite else pal.text_muted,
            tooltip="收藏",
        )

        def _toggle_fav(_e) -> None:
            cred.favorite = not cred.favorite
            vault.update(cred)
            fav_btn.icon = ft.Icons.STAR if cred.favorite else ft.Icons.STAR_BORDER
            fav_btn.icon_color = pal.warning if cred.favorite else pal.text_muted
            if self.state.page:
                self.state.page.update()

        fav_btn.on_click = _toggle_fav

        def _soft_del(_e) -> None:
            vault.soft_delete(cred.id)
            self.state.selected_id = None
            self._cred = None
            self.state.toast("已移入回收站")
            self.on_changed()
            if self._show_back and self.on_back:
                self.on_back()

        def _restore(_e) -> None:
            vault.restore(cred.id)
            self.state.toast("已从回收站恢复")
            self.on_changed()

        def _purge(_e) -> None:
            vault.delete(cred.id)
            self.state.selected_id = None
            self._cred = None
            self.state.toast("已彻底删除")
            self.on_changed()
            if self._show_back and self.on_back:
                self.on_back()

        if cred.is_deleted:
            action_btns = ft.Row(
                [
                    ft.FilledButton("恢复", icon=ft.Icons.RESTORE_FROM_TRASH, on_click=_restore),
                    ft.OutlinedButton("彻底删除", icon=ft.Icons.DELETE_FOREVER, on_click=_purge),
                ],
                spacing=8,
            )
        else:
            action_btns = ft.Row(
                [
                    fav_btn,
                    ft.FilledTonalButton("编辑", icon=ft.Icons.EDIT_OUTLINED, on_click=lambda _e: self.on_edit(cred)),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=pal.danger, tooltip="移入回收站", on_click=_soft_del),
                ],
                spacing=4,
            )

        if self._show_back and self.on_back is not None:
            blocks.append(
                ft.Row(
                    [
                        ft.IconButton(ft.Icons.ARROW_BACK, tooltip="返回列表", on_click=lambda _e: self.on_back()),
                        action_btns,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
            blocks.append(
                ft.Row(
                    [avatar, title_col],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        else:
            blocks.append(
                ft.Row(
                    [ft.Row([avatar, title_col], spacing=12, expand=True, vertical_alignment=ft.CrossAxisAlignment.CENTER), action_btns],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )

        # 健康徽章 + 标签胶囊
        markers = vault.markers(cred, old_days=self.state.old_days())
        meta_chips: list[ft.Control] = []
        for label, color in markers:
            meta_chips.append(
                pill(
                    f"{label}密码",
                    color,
                    pal.warning_soft if label == "重" else pal.danger_soft if label == "弱" else pal.surface_alt,
                )
            )
        for tag in cred.tags:
            meta_chips.append(pill(f"# {tag}", pal.primary, pal.primary_soft))
        if meta_chips:
            blocks.append(ft.Row(meta_chips, wrap=True, spacing=6, run_spacing=6))

        # 2. 基础凭证卡（账号 / 密码 / 网址）
        blocks.append(self._build_credentials_card(cred, pal))

        # 3. 2FA 动态验证码卡
        if (cred.totp_secret or "").strip():
            blocks.append(self._build_totp_card(cred, pal))

        # 4. 密保联络卡（手机号 / 邮箱）
        if (cred.recovery_phone or "").strip() or (cred.recovery_email or "").strip():
            blocks.append(self._build_contact_card(cred, pal))

        # 5. 其他兜底卡（密保问答 / 备用恢复码组 / 自定义文本）
        if cred.recovery_items:
            blocks.append(self._build_recovery_items_card(cred, pal))

        # 6. 账号相关密钥卡（GitHub Token / API Key 等）
        if cred.api_keys:
            blocks.append(self._build_api_keys_card(cred, pal))

        # 7. 备注卡
        if (cred.notes or "").strip():
            blocks.append(
                card(
                    ft.Column(
                        [
                            section_title("备注", pal, ft.Icons.NOTES),
                            ft.Text(cred.notes, size=13, color=pal.text, selectable=True),
                        ],
                        spacing=8,
                    ),
                    pal,
                )
            )

        # 底部时间信息（精简格式）
        c_time = (cred.created_at or "")[:16].replace("T", " ")
        u_time = (cred.updated_at or "")[:16].replace("T", " ")
        blocks.append(
            ft.Text(
                f"创建 {c_time} · 更新 {u_time} · 密码已用 {cred.age_days()} 天",
                size=11,
                color=pal.text_faint,
            )
        )

        self._scroll_col.controls = blocks
        self._container.content = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            content=self._scroll_col,
        )

    # ------------------------------------------------------------ 原地更新字段行（绝不跳顶）
    def _field_row(
        self,
        label: str,
        value: str,
        pal: Palette,
        *,
        mono: bool = False,
        secret: bool = False,
        revealed: bool = True,
        on_copy: Optional[Callable[[], None]] = None,
        extra_btn: Optional[ft.Control] = None,
        on_state_change: Optional[Callable[[bool], None]] = None,
    ) -> ft.Control:
        state_box = {"rev": revealed}
        val_txt = ft.Text(
            (value if revealed else ("••••••••••••" if value else "—")) or "—",
            size=14,
            color=pal.text,
            font_family=MONO_FONT if mono else None,
            selectable=revealed,
        )

        btns: list[ft.Control] = []
        if extra_btn is not None:
            btns.append(extra_btn)

        if secret:
            eye_btn = ft.IconButton(
                ft.Icons.VISIBILITY_OFF if revealed else ft.Icons.VISIBILITY,
                icon_size=18,
                tooltip="隐藏" if revealed else "显示",
            )

            def _toggle(_e) -> None:
                self.state.touch_activity()
                state_box["rev"] = not state_box["rev"]
                rev_now = state_box["rev"]
                val_txt.value = (value if rev_now else ("••••••••••••" if value else "—")) or "—"
                val_txt.selectable = rev_now
                eye_btn.icon = ft.Icons.VISIBILITY_OFF if rev_now else ft.Icons.VISIBILITY
                eye_btn.tooltip = "隐藏" if rev_now else "显示"
                if on_state_change is not None:
                    on_state_change(rev_now)
                if self.state.page:
                    self.state.page.update()

            eye_btn.on_click = _toggle
            btns.append(eye_btn)

        if on_copy is not None and value:
            btns.append(
                ft.IconButton(ft.Icons.COPY, icon_size=18, tooltip=f"复制{label}", on_click=lambda _e: on_copy())
            )
        return ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(label, size=11, color=pal.text_faint),
                        val_txt,
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.Row(btns, spacing=0, tight=True),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_credentials_card(self, cred: Credential, pal: Palette) -> ft.Control:
        rows: list[ft.Control] = [section_title("登录凭证", pal, ft.Icons.LOCK_OUTLINE)]
        if cred.username:
            rows.append(
                self._field_row(
                    "账号 / 用户名",
                    cred.username,
                    pal,
                    on_copy=lambda: self.state.copy_text(cred.username, "账号"),
                )
            )

        st = crypto.password_strength(cred.password)
        st_colors = [pal.danger, pal.danger, pal.warning, pal.success, pal.success]

        hist_pw_texts: list[tuple[ft.Text, str]] = []

        def _on_pw_rev(rev_now: bool) -> None:
            self._show_password = rev_now
            for txt_ctrl, raw_hpw in hist_pw_texts:
                txt_ctrl.value = raw_hpw if rev_now else "••••••••"

        rows.append(
            self._field_row(
                f"密码（强度：{st['label']}）",
                cred.password,
                pal,
                mono=True,
                secret=True,
                revealed=self._show_password,
                on_copy=lambda: self.state.copy_text(cred.password, "密码", is_secret=True),
                on_state_change=_on_pw_rev,
            )
        )
        rows.append(
            ft.ProgressBar(value=max(0.08, (st["score"] + 1) / 5.0), color=st_colors[st["score"]], bgcolor=pal.surface_alt, height=4)
        )

        if cred.url:
            def _open_url(_e) -> None:
                u = cred.url.strip()
                if u and "://" not in u:
                    u = "https://" + u
                if self.state.page:
                    try:
                        self.state.page.launch_url(u)
                    except Exception:
                        self.state.copy_text(u, "网址")

            rows.append(
                self._field_row(
                    "网址",
                    cred.url,
                    pal,
                    on_copy=lambda: self.state.copy_text(cred.url, "网址"),
                    extra_btn=ft.IconButton(ft.Icons.OPEN_IN_NEW, icon_size=18, tooltip="打开网址", on_click=_open_url),
                )
            )

        if cred.password_history:
            hist_rows: list[ft.Control] = []
            for h in cred.password_history:
                hpw = h.get("password", "")
                htxt = ft.Text(hpw if self._show_password else "••••••••", font_family=MONO_FONT, size=12, color=pal.text_muted, expand=True)
                hist_pw_texts.append((htxt, hpw))
                hist_rows.append(
                    ft.Row(
                        [
                            ft.Text(h.get("changed_at", "")[:10], size=11, color=pal.text_faint, width=82),
                            htxt,
                            ft.IconButton(
                                ft.Icons.COPY,
                                icon_size=16,
                                tooltip="复制历史密码",
                                on_click=lambda _e, p=hpw: self.state.copy_text(p, "历史密码", is_secret=True),
                            ),
                        ],
                        spacing=6,
                    )
                )
            hist_box = ft.Column(hist_rows, spacing=4, visible=self._show_history)
            hist_btn = ft.TextButton(
                f"{'收起' if self._show_history else '查看'}历史密码（{len(cred.password_history)} 条）",
                icon=ft.Icons.HISTORY,
            )

            def _toggle_hist(_e) -> None:
                self._show_history = not self._show_history
                hist_box.visible = self._show_history
                hist_btn.text = f"{'收起' if self._show_history else '查看'}历史密码（{len(cred.password_history)} 条）"
                if self.state.page:
                    self.state.page.update()

            hist_btn.on_click = _toggle_hist
            rows.append(hist_btn)
            rows.append(hist_box)
        return card(ft.Column(rows, spacing=10), pal)

    def _build_totp_card(self, cred: Credential, pal: Palette) -> ft.Control:
        try:
            code, rem, period = totp.generate_totp(cred.totp_secret)
            self._totp_code_txt = ft.Text(
                totp.format_code(code),
                font_family=MONO_FONT,
                size=24,
                weight=ft.FontWeight.BOLD,
                color=pal.primary,
                selectable=True,
            )
            self._totp_rem_txt = ft.Text(f"{rem}s", size=12, color=pal.text_muted, width=32, text_align=ft.TextAlign.RIGHT)
            self._totp_bar = ft.ProgressBar(
                value=max(0.0, min(1.0, rem / float(period))),
                color=pal.warning if rem <= 5 else pal.primary,
                bgcolor=pal.surface_alt,
                height=5,
            )

            def _copy_totp(_e) -> None:
                c_now, _, _ = totp.generate_totp(cred.totp_secret)
                self.state.copy_text(c_now, "2FA 动态码", is_secret=True)

            body = ft.Column(
                [
                    section_title("2FA 两步验证码（TOTP）", pal, ft.Icons.VERIFIED_USER_OUTLINED),
                    ft.Row(
                        [
                            self._totp_code_txt,
                            ft.Row(
                                [
                                    self._totp_rem_txt,
                                    ft.FilledTonalButton("复制", icon=ft.Icons.COPY, on_click=_copy_totp),
                                ],
                                spacing=6,
                                tight=True,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        wrap=True,
                    ),
                    self._totp_bar,
                ],
                spacing=10,
            )
        except Exception as exc:
            body = ft.Column(
                [
                    section_title("2FA 两步验证码（TOTP）", pal, ft.Icons.VERIFIED_USER_OUTLINED),
                    ft.Text(f"2FA 密钥解析失败：{exc}", size=12, color=pal.danger),
                ],
                spacing=6,
            )
        return card(body, pal)

    def _build_contact_card(self, cred: Credential, pal: Palette) -> ft.Control:
        rows: list[ft.Control] = [section_title("密保与找回联络", pal, ft.Icons.CONTACT_PHONE_OUTLINED)]
        if cred.recovery_phone:
            rows.append(
                self._field_row(
                    "密保手机号",
                    cred.recovery_phone,
                    pal,
                    mono=True,
                    on_copy=lambda: self.state.copy_text(cred.recovery_phone, "密保手机号"),
                )
            )
        if cred.recovery_email:
            rows.append(
                self._field_row(
                    "密保 / 绑定邮箱",
                    cred.recovery_email,
                    pal,
                    on_copy=lambda: self.state.copy_text(cred.recovery_email, "密保邮箱"),
                )
            )
        return card(ft.Column(rows, spacing=10), pal)

    def _build_recovery_items_card(self, cred: Credential, pal: Palette) -> ft.Control:
        vault = self.state.vault
        rows: list[ft.Control] = [section_title("其他兜底信息", pal, ft.Icons.HEALTH_AND_SAFETY_OUTLINED)]
        for idx, item in enumerate(cred.recovery_items):
            itype = item.get("type", "text")
            if itype == "qa":
                q = item.get("question", "密保问题")
                a = item.get("answer", "")
                k_id = f"qa_{idx}"

                def _on_qa(rev_now: bool, kid=k_id) -> None:
                    if rev_now:
                        self._revealed_keys.add(kid)
                    else:
                        self._revealed_keys.discard(kid)

                rows.append(
                    self._field_row(
                        f"密保问题：{q}",
                        a,
                        pal,
                        secret=True,
                        revealed=(k_id in self._revealed_keys),
                        on_copy=lambda ans=a: self.state.copy_text(ans, "密保答案", is_secret=True),
                        on_state_change=_on_qa,
                    )
                )
            elif itype == "backup_codes":
                lbl = item.get("label") or "备用恢复码组"
                codes = item.get("codes") or []
                unused = sum(1 for c in codes if not c.get("used"))
                header_txt = ft.Text(f"{lbl}（剩余 {unused}/{len(codes)} 组，点击划掉已用）", size=12, color=pal.text_muted, expand=True)
                chips: list[ft.Control] = []
                for c_idx, c_obj in enumerate(codes):
                    code_str = str(c_obj.get("code", ""))
                    used = bool(c_obj.get("used", False))
                    ic = ft.Icon(
                        ft.Icons.CHECK_CIRCLE if used else ft.Icons.RADIO_BUTTON_UNCHECKED,
                        size=14,
                        color=pal.text_faint if used else pal.primary,
                    )
                    ct = ft.Text(
                        code_str,
                        font_family=MONO_FONT,
                        size=12,
                        color=pal.text_faint if used else pal.text,
                        style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if used else ft.TextDecoration.NONE),
                    )

                    def _toggle_used(_e, ii=idx, ci=c_idx, ic_ref=ic, ct_ref=ct, htxt=header_txt, l_str=lbl, c_list=codes) -> None:
                        if vault is None:
                            return
                        new_used = not bool(cred.recovery_items[ii]["codes"][ci].get("used"))
                        cred.recovery_items[ii]["codes"][ci]["used"] = new_used
                        vault.update(cred)
                        ic_ref.name = ft.Icons.CHECK_CIRCLE if new_used else ft.Icons.RADIO_BUTTON_UNCHECKED
                        ic_ref.color = pal.text_faint if new_used else pal.primary
                        ct_ref.color = pal.text_faint if new_used else pal.text
                        ct_ref.style = ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if new_used else ft.TextDecoration.NONE)
                        rem_cnt = sum(1 for x in c_list if not x.get("used"))
                        htxt.value = f"{l_str}（剩余 {rem_cnt}/{len(c_list)} 组，点击划掉已用）"
                        if self.state.page:
                            self.state.page.update()

                    chips.append(
                        ft.Container(
                            content=ft.Row([ic, ct], spacing=5, tight=True),
                            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                            border_radius=8,
                            bgcolor=pal.surface_alt,
                            border=ft.Border.all(1, pal.border),
                            on_click=_toggle_used,
                            tooltip="点击标记/取消已使用",
                        )
                    )
                rows.append(
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    header_txt,
                                    ft.TextButton(
                                        "复制未用码",
                                        on_click=lambda _e, cs=codes: self.state.copy_text(
                                            "\n".join(str(x.get("code", "")) for x in cs if not x.get("used")),
                                            "未用备用码",
                                            is_secret=True,
                                        ),
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(chips, wrap=True, spacing=8, run_spacing=8),
                        ],
                        spacing=6,
                    )
                )
            else:
                lbl = item.get("label") or "兜底说明"
                val = item.get("value") or ""
                rows.append(
                    self._field_row(lbl, val, pal, on_copy=lambda v=val, l=lbl: self.state.copy_text(v, l, is_secret=True))
                )
        return card(ft.Column(rows, spacing=12), pal)

    def _build_api_keys_card(self, cred: Credential, pal: Palette) -> ft.Control:
        rows: list[ft.Control] = [section_title("账号相关密钥（Token / API Key）", pal, ft.Icons.VPN_KEY_OUTLINED)]
        for idx, k in enumerate(cred.api_keys):
            lbl = k.get("label") or f"密钥 #{idx + 1}"
            val = k.get("value") or ""
            note = k.get("note") or ""
            kid = f"api_{idx}"

            def _on_api(rev_now: bool, key_id=kid) -> None:
                if rev_now:
                    self._revealed_keys.add(key_id)
                else:
                    self._revealed_keys.discard(key_id)

            title_lbl = f"{lbl} · {note}" if note else lbl
            rows.append(
                self._field_row(
                    title_lbl,
                    val,
                    pal,
                    mono=True,
                    secret=True,
                    revealed=(kid in self._revealed_keys),
                    on_copy=lambda v=val, l=lbl: self.state.copy_text(v, l, is_secret=True),
                    on_state_change=_on_api,
                )
            )
        return card(ft.Column(rows, spacing=10), pal)
