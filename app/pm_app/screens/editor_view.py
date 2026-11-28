"""全字段分块账号编辑器（支持 9 类字段完整录入与动态增删）。"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

import flet as ft

from pm_core import Credential, totp
from ..state import AppState
from ..theme import MONO_FONT, card, section_title
from .dialogs import open_generator_dialog


class EditorView:
    def __init__(
        self,
        state: AppState,
        *,
        on_saved: Callable[[Credential], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self.state = state
        self.on_saved = on_saved
        self.on_cancel = on_cancel

        self._editing: Optional[Credential] = None
        self._recovery_items: List[Dict[str, Any]] = []
        self._api_keys: List[Dict[str, Any]] = []
        self._container = ft.Container(expand=True)

    def control(self) -> ft.Control:
        return self._container

    def load(self, cred: Optional[Credential]) -> None:
        self._editing = cred
        self._recovery_items = copy.deepcopy(cred.recovery_items) if cred else []
        self._api_keys = copy.deepcopy(cred.api_keys) if cred else []
        self._build_form()

    def _build_form(self) -> None:
        pal = self.state.palette()
        c = self._editing

        self.tf_name = ft.TextField(label="标题 *", value=c.name if c else "", hint_text="如：GitHub、Google、招商银行", dense=True)
        self.tf_url = ft.TextField(label="网址", value=c.url if c else "", hint_text="https://...", dense=True)
        self.tf_user = ft.TextField(label="账号 / 用户名", value=c.username if c else "", dense=True)
        self.tf_pass = ft.TextField(
            label="密码",
            value=c.password if c else "",
            password=True,
            can_reveal_password=True,
            dense=True,
            expand=True,
        )
        self.tf_totp = ft.TextField(
            label="2FA 密钥（Base32 或 otpauth://totp/...）",
            value=c.totp_secret if c else "",
            hint_text="留空表示不启用 2FA",
            dense=True,
        )
        self.totp_preview = ft.Text("", size=12, color=pal.text_muted)

        def _check_totp(_e=None) -> None:
            raw = (self.tf_totp.value or "").strip()
            if not raw:
                self.totp_preview.value = ""
            else:
                try:
                    code, rem, _ = totp.generate_totp(raw)
                    self.totp_preview.value = f"预览验证码：{totp.format_code(code)}（剩余 {rem}s）"
                    self.totp_preview.color = pal.success
                except Exception as exc:
                    self.totp_preview.value = f"格式无效：{exc}"
                    self.totp_preview.color = pal.danger
            if self.state.page:
                self.state.page.update()

        self.tf_totp.on_change = _check_totp
        if c and c.totp_secret:
            _check_totp()

        self.tf_phone = ft.TextField(label="密保手机号", value=c.recovery_phone if c else "", dense=True)
        self.tf_email = ft.TextField(label="密保 / 绑定邮箱", value=c.recovery_email if c else "", dense=True)
        self.tf_tags = ft.TextField(label="标签（逗号分隔）", value=", ".join(c.tags) if c else "", hint_text="如：开发, 金融, 高频", dense=True)
        self.tf_notes = ft.TextField(label="备注", value=c.notes if c else "", multiline=True, min_lines=3, max_lines=6, dense=True)
        self.cb_fav = ft.Checkbox(label="标记为收藏", value=bool(c.favorite) if c else False)
        self.err_txt = ft.Text("", size=12, color=pal.danger)

        self._recovery_col = ft.Column([], spacing=10)
        self._apikeys_col = ft.Column([], spacing=10)
        self._render_dynamic_lists()

        def _use_generated(pwd: str) -> None:
            self.tf_pass.value = pwd
            if self.state.page:
                self.state.page.update()

        def _save(_e=None) -> None:
            vault = self.state.vault
            if vault is None:
                return
            name = (self.tf_name.value or "").strip()
            if not name:
                self.err_txt.value = "标题不能为空"
                if self.state.page:
                    self.state.page.update()
                return
            tags = [t.strip() for t in (self.tf_tags.value or "").replace("，", ",").split(",") if t.strip()]
            clean_rec = self._collect_recovery_items()
            clean_keys = self._collect_api_keys()

            if self._editing is not None:
                target = Credential.from_dict(self._editing.to_dict())
                target.name = name
                target.url = (self.tf_url.value or "").strip()
                target.username = (self.tf_user.value or "").strip()
                target.password = self.tf_pass.value or ""
                target.totp_secret = (self.tf_totp.value or "").strip()
                target.recovery_phone = (self.tf_phone.value or "").strip()
                target.recovery_email = (self.tf_email.value or "").strip()
                target.recovery_items = clean_rec
                target.api_keys = clean_keys
                target.tags = tags
                target.notes = self.tf_notes.value or ""
                target.favorite = bool(self.cb_fav.value)
                saved = vault.update(target)
                self.state.toast("修改已保存")
            else:
                new_c = Credential(
                    name=name,
                    url=(self.tf_url.value or "").strip(),
                    username=(self.tf_user.value or "").strip(),
                    password=self.tf_pass.value or "",
                    totp_secret=(self.tf_totp.value or "").strip(),
                    recovery_phone=(self.tf_phone.value or "").strip(),
                    recovery_email=(self.tf_email.value or "").strip(),
                    recovery_items=clean_rec,
                    api_keys=clean_keys,
                    tags=tags,
                    notes=self.tf_notes.value or "",
                    favorite=bool(self.cb_fav.value),
                )
                saved = vault.add(new_c)
                self.state.toast("新账号已保存")
            self.on_saved(saved)

        header = ft.Row(
            [
                ft.Row(
                    [
                        ft.IconButton(ft.Icons.CLOSE, tooltip="取消", on_click=lambda _e: self.on_cancel()),
                        ft.Text("编辑账号" if c else "新增账号", size=19, weight=ft.FontWeight.BOLD, color=pal.text),
                    ],
                    spacing=6,
                ),
                ft.FilledButton("保存", icon=ft.Icons.CHECK, on_click=_save, height=40),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # 动态添加按钮
        def _add_qa(_e) -> None:
            self._recovery_items.append({"type": "qa", "question": "", "answer": ""})
            self._render_dynamic_lists()
            if self.state.page:
                self.state.page.update()

        def _add_codes(_e) -> None:
            self._recovery_items.append({"type": "backup_codes", "label": "备用恢复码组", "codes": []})
            self._render_dynamic_lists()
            if self.state.page:
                self.state.page.update()

        def _add_text_rec(_e) -> None:
            self._recovery_items.append({"type": "text", "label": "其他兜底", "value": ""})
            self._render_dynamic_lists()
            if self.state.page:
                self.state.page.update()

        def _add_apikey(_e) -> None:
            self._api_keys.append({"label": "", "value": "", "note": ""})
            self._render_dynamic_lists()
            if self.state.page:
                self.state.page.update()

        form = ft.Column(
            [
                header,
                self.err_txt,
                card(
                    ft.Column(
                        [
                            section_title("基础账号与密码", pal, ft.Icons.LOCK_OUTLINE),
                            self.tf_name,
                            self.tf_url,
                            self.tf_user,
                            ft.Row(
                                [
                                    self.tf_pass,
                                    ft.OutlinedButton(
                                        "生成",
                                        icon=ft.Icons.AUTO_AWESOME,
                                        on_click=lambda _e: open_generator_dialog(self.state, on_use=_use_generated),
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            self.tf_totp,
                            self.totp_preview,
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    pal,
                ),
                card(
                    ft.Column(
                        [
                            section_title("密保手机号与邮箱", pal, ft.Icons.CONTACT_PHONE_OUTLINED),
                            self.tf_phone,
                            self.tf_email,
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    pal,
                ),
                card(
                    ft.Column(
                        [
                            section_title("其他兜底（密保问答 / 谷歌10组验证数字 / 自定义）", pal, ft.Icons.HEALTH_AND_SAFETY_OUTLINED),
                            self._recovery_col,
                            ft.Row(
                                [
                                    ft.OutlinedButton("+ 密保问答", on_click=_add_qa),
                                    ft.OutlinedButton("+ 备用恢复码组", on_click=_add_codes),
                                    ft.OutlinedButton("+ 自定义兜底", on_click=_add_text_rec),
                                ],
                                wrap=True,
                                spacing=8,
                            ),
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    pal,
                ),
                card(
                    ft.Column(
                        [
                            section_title("账号相关密钥（GitHub Token / API Key 等）", pal, ft.Icons.VPN_KEY_OUTLINED),
                            self._apikeys_col,
                            ft.OutlinedButton("+ 添加密钥", icon=ft.Icons.ADD, on_click=_add_apikey),
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    pal,
                ),
                card(
                    ft.Column(
                        [
                            section_title("备注与分组", pal, ft.Icons.NOTES),
                            self.tf_tags,
                            self.tf_notes,
                            self.cb_fav,
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    pal,
                ),
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self._container.content = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=20, vertical=16),
            content=form,
        )

    # ------------------------------------------------------------ 动态列表渲染与收集
    def _render_dynamic_lists(self) -> None:
        pal = self.state.palette()
        self._rec_widgets: list[dict] = []
        rec_controls: list[ft.Control] = []

        for idx, item in enumerate(self._recovery_items):
            itype = item.get("type", "text")

            def _del_rec(_e, i=idx) -> None:
                self._recovery_items = self._collect_recovery_items(keep_empty=True)
                self._recovery_items.pop(i)
                self._render_dynamic_lists()
                if self.state.page:
                    self.state.page.update()

            if itype == "qa":
                q_tf = ft.TextField(label="密保问题", value=item.get("question", ""), dense=True)
                a_tf = ft.TextField(label="密保答案", value=item.get("answer", ""), dense=True)
                self._rec_widgets.append({"type": "qa", "q": q_tf, "a": a_tf})
                rec_controls.append(
                    card(
                        ft.Column(
                            [
                                ft.Row(
                                    [ft.Text("密保问答", size=12, color=pal.text_muted), ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18, on_click=_del_rec)],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                q_tf,
                                a_tf,
                            ],
                            spacing=6,
                        ),
                        pal,
                        padding=10,
                        bgcolor=pal.surface_alt,
                    )
                )
            elif itype == "backup_codes":
                lbl_tf = ft.TextField(label="名称", value=item.get("label", "备用恢复码组"), dense=True)
                existing_lines = "\n".join(
                    (f"[已用] {c.get('code', '')}" if c.get("used") else str(c.get("code", "")))
                    for c in (item.get("codes") or [])
                )
                codes_tf = ft.TextField(
                    label="恢复码列表（每行一组，直接粘贴 Google 10 组数字即可）",
                    value=existing_lines,
                    multiline=True,
                    min_lines=3,
                    max_lines=6,
                    dense=True,
                )
                self._rec_widgets.append({"type": "backup_codes", "label": lbl_tf, "codes": codes_tf})
                rec_controls.append(
                    card(
                        ft.Column(
                            [
                                ft.Row(
                                    [ft.Text("备用恢复码组", size=12, color=pal.text_muted), ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18, on_click=_del_rec)],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                lbl_tf,
                                codes_tf,
                            ],
                            spacing=6,
                        ),
                        pal,
                        padding=10,
                        bgcolor=pal.surface_alt,
                    )
                )
            else:
                lbl_tf = ft.TextField(label="兜底项名称", value=item.get("label", "其他兜底"), dense=True)
                val_tf = ft.TextField(label="内容", value=item.get("value", ""), multiline=True, min_lines=2, max_lines=4, dense=True)
                self._rec_widgets.append({"type": "text", "label": lbl_tf, "value": val_tf})
                rec_controls.append(
                    card(
                        ft.Column(
                            [
                                ft.Row(
                                    [ft.Text("自定义兜底", size=12, color=pal.text_muted), ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18, on_click=_del_rec)],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                lbl_tf,
                                val_tf,
                            ],
                            spacing=6,
                        ),
                        pal,
                        padding=10,
                        bgcolor=pal.surface_alt,
                    )
                )
        self._recovery_col.controls = rec_controls

        self._key_widgets: list[dict] = []
        key_controls: list[ft.Control] = []
        for idx, k in enumerate(self._api_keys):
            def _del_k(_e, i=idx) -> None:
                self._api_keys = self._collect_api_keys(keep_empty=True)
                self._api_keys.pop(i)
                self._render_dynamic_lists()
                if self.state.page:
                    self.state.page.update()

            l_tf = ft.TextField(label="密钥名称", value=k.get("label", ""), hint_text="如：GitHub PAT / OpenAI Key", dense=True)
            v_tf = ft.TextField(label="密钥值", value=k.get("value", ""), password=True, can_reveal_password=True, dense=True)
            n_tf = ft.TextField(label="用途/过期备注（可选）", value=k.get("note", ""), dense=True)
            self._key_widgets.append({"label": l_tf, "value": v_tf, "note": n_tf})
            key_controls.append(
                card(
                    ft.Column(
                        [
                            ft.Row(
                                [ft.Text(f"密钥 #{idx + 1}", size=12, color=pal.text_muted), ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18, on_click=_del_k)],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            l_tf,
                            v_tf,
                            n_tf,
                        ],
                        spacing=6,
                    ),
                    pal,
                    padding=10,
                    bgcolor=pal.surface_alt,
                )
            )
        self._apikeys_col.controls = key_controls

    def _collect_recovery_items(self, *, keep_empty: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for w in getattr(self, "_rec_widgets", []):
            t = w["type"]
            if t == "qa":
                q, a = (w["q"].value or "").strip(), (w["a"].value or "").strip()
                if keep_empty or q or a:
                    out.append({"type": "qa", "question": q, "answer": a})
            elif t == "backup_codes":
                lbl = (w["label"].value or "").strip() or "备用恢复码组"
                raw_lines = (w["codes"].value or "").splitlines()
                codes = []
                for line in raw_lines:
                    s = line.strip()
                    if not s:
                        continue
                    used = False
                    if s.startswith("[已用]"):
                        used = True
                        s = s[4:].strip()
                    if s:
                        codes.append({"code": s, "used": used})
                if keep_empty or codes:
                    out.append({"type": "backup_codes", "label": lbl, "codes": codes})
            else:
                lbl = (w["label"].value or "").strip() or "其他兜底"
                val = (w["value"].value or "").strip()
                if keep_empty or val:
                    out.append({"type": "text", "label": lbl, "value": val})
        return out

    def _collect_api_keys(self, *, keep_empty: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for w in getattr(self, "_key_widgets", []):
            lbl = (w["label"].value or "").strip()
            val = (w["value"].value or "").strip()
            note = (w["note"].value or "").strip()
            if keep_empty or lbl or val:
                out.append({"label": lbl or "未命名密钥", "value": val, "note": note})
        return out
