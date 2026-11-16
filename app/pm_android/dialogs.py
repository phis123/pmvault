"""Android 版弹层：确认框、信息框、条目编辑、生成器、同步、设置。

Flet 1.0 的用法：page.show_dialog(dialog) / page.pop_dialog()，
对话框本体从 page.overlay 里进出。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import flet as ft

from pm_core import Credential, crypto, generator, sorting, sync


# ---------------------------------------------------------------- 基础弹层

def info_dialog(page: ft.Page, title: str, message: str, on_close: Optional[Callable] = None) -> None:
    def close(_e=None):
        page.pop_dialog()
        if on_close:
            on_close()

    page.show_dialog(ft.AlertDialog(
        title=ft.Text(title),
        content=ft.Text(message, selectable=True),
        actions=[ft.TextButton("知道了", on_click=close)],
        modal=True,
    ))


def confirm_dialog(page: ft.Page, title: str, message: str, on_confirm: Callable, danger: bool = False) -> None:
    def yes(_e=None):
        page.pop_dialog()
        on_confirm()

    page.show_dialog(ft.AlertDialog(
        title=ft.Text(title),
        content=ft.Text(message),
        actions=[
            ft.TextButton("取消", on_click=lambda _e: page.pop_dialog()),
            ft.TextButton("确定" if not danger else "确定删除", on_click=yes),
        ],
        modal=True,
    ))


def recovery_key_dialog(page: ft.Page, key_text: str, reason: str = "") -> None:
    """恢复密钥强制确认：不勾选不允许关闭。"""
    ack = ft.Checkbox(label="我已抄写并妥善保存", value=False)
    done_button = ft.TextButton("完成", disabled=True)

    def on_ack(_e=None):
        done_button.disabled = not ack.value
        page.update()

    ack.on_change = on_ack
    notes = [ft.Text(reason)] if reason else []
    notes.append(ft.Text(
        "这是主密码遗忘时唯一的找回方式。建议抄到纸上或离线保存，不要截图发到聊天软件。",
        size=13, color="#7c5511",
    ))

    def done(_e=None):
        if not ack.value:
            return
        page.pop_dialog()

    done_button.on_click = done
    page.show_dialog(ft.AlertDialog(
        title=ft.Text("请抄写恢复密钥"),
        content=ft.Column([
            *notes,
            ft.Container(
                content=ft.Text(key_text, font_family="monospace", size=20, selectable=True,
                                text_align=ft.TextAlign.CENTER),
                padding=14, border=ft.Border.all(1, "#2f6fed"), border_radius=10,
                bgcolor="#f2f6ff",
            ),
            ack,
        ], spacing=10, tight=True),
        actions=[done_button],
        modal=True,
    ))


def copy_text(page: ft.Page, text: str, what: str = "内容") -> None:
    """复制到剪贴板（Flet 1.0 用 Clipboard 控件）。"""
    if not text:
        return
    try:
        page.set_clipboard(text)
    except Exception:
        try:
            page.overlay.append(ft.Clipboard())
            page.set_clipboard(text)
        except Exception:
            info_dialog(page, "复制失败", "系统未允许访问剪贴板，请手动长按选择复制。")
            return
    page.show_dialog(ft.SnackBar(ft.Text(f"已复制{what}"), duration=1500))
    page.update()


# ---------------------------------------------------------------- 条目编辑

def credential_dialog(page: ft.Page, defaults: Dict[str, Any], cred: Optional[Credential],
                      on_save: Callable[[Credential], None], on_delete: Optional[Callable] = None) -> None:
    """新增 / 编辑条目。返回保存后的 Credential 交给调用方处理。"""
    target = cred or Credential()

    name = ft.TextField(label="名称 *", value=target.name, autofocus=not bool(cred))
    username = ft.TextField(label="用户名", value=target.username)
    password = ft.TextField(label="密码", value=target.password, password=True, can_reveal_password=True)
    url = ft.TextField(label="网址", value=target.url)
    tags = ft.TextField(label="标签", value=", ".join(target.tags), hint_text="用逗号分隔，例如：工作, 邮箱")
    notes = ft.TextField(label="备注", value=target.notes, multiline=True, min_lines=2, max_lines=4)
    error = ft.Text("", color="#c0392b", size=13)

    custom_rows: List[Dict[str, Any]] = []
    custom_box = ft.Column([], spacing=6)

    def add_custom(label: str = "", value: str = "", secret: bool = True) -> None:
        label_field = ft.TextField(label="字段名", value=label, width=120)
        value_field = ft.TextField(label="值", value=value, password=secret, can_reveal_password=secret, expand=True)

        def remove(_e=None):
            custom_box.controls.remove(row)
            custom_rows.remove(record)
            page.update()

        row = ft.Row([
            label_field, value_field,
            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="删除该字段", on_click=remove),
        ], spacing=6)
        record = {"label": label_field, "value": value_field, "secret": secret, "row": row}
        custom_rows.append(record)
        custom_box.controls.append(row)

    for field in (target.custom_fields or []):
        add_custom(field.get("label", ""), field.get("value", ""), bool(field.get("secret", True)))

    def open_generator(_e=None) -> None:
        generator_dialog(page, defaults, on_use=lambda value: (setattr(password, "value", value), page.update()))

    def collect() -> Optional[Credential]:
        name_text = (name.value or "").strip()
        if not name_text:
            error.value = "名称不能为空"
            page.update()
            return None
        obj = cred or Credential()
        obj.name = name_text
        obj.username = username.value or ""
        obj.password = password.value or ""
        obj.url = url.value or ""
        obj.notes = notes.value or ""
        obj.tags = [t.strip() for t in (tags.value or "").replace("，", ",").split(",") if t.strip()]
        obj.custom_fields = [
            {"label": (r["label"].value or "").strip(), "value": r["value"].value or "", "secret": bool(r["secret"])}
            for r in custom_rows if (r["label"].value or "").strip()
        ]
        try:
            obj.validate()
        except Exception as exc:
            error.value = str(exc)
            page.update()
            return None
        return obj

    def save(_e=None) -> None:
        obj = collect()
        if obj is None:
            return
        page.pop_dialog()
        on_save(obj)

    actions = [ft.TextButton("取消", on_click=lambda _e: page.pop_dialog()),
               ft.FilledButton("保存", on_click=save)]
    if cred and on_delete:
        def ask_delete(_e=None) -> None:
            page.pop_dialog()
            confirm_dialog(page, "删除条目", f"确定要删除「{cred.name}」吗？此操作不可撤销。",
                           on_confirm=lambda: on_delete(cred), danger=True)

        actions.insert(0, ft.TextButton("删除", on_click=ask_delete))

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("编辑条目" if cred else "新增条目"),
        content=ft.Container(
            content=ft.Column([
                name, username,
                ft.Row([password, ft.TextButton("生成", on_click=open_generator)]),
                url, tags, notes,
                ft.Row([ft.Text("自定义字段", size=13, color="#6b7889"),
                        ft.TextButton("+ 添加", on_click=lambda _e: (add_custom(), page.update()))]),
                custom_box,
                error,
            ], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO),
            width=340, height=460,
        ),
        actions=actions,
        modal=True,
    ))


# ---------------------------------------------------------------- 生成器

def generator_dialog(page: ft.Page, defaults: Dict[str, Any], on_use: Callable[[str], None]) -> None:
    options = {**generator.DEFAULT_OPTIONS, **(defaults or {})}
    length_label = ft.Text(str(int(options["length"])), width=30)
    preview = ft.Text("", font_family="monospace", selectable=True, size=15)
    error = ft.Text("", color="#c0392b", size=13)

    checks = {
        "lower": ft.Checkbox(label="小写 a-z", value=bool(options["lower"])),
        "upper": ft.Checkbox(label="大写 A-Z", value=bool(options["upper"])),
        "digits": ft.Checkbox(label="数字 0-9", value=bool(options["digits"])),
        "symbols": ft.Checkbox(label="符号 !@#$", value=bool(options["symbols"])),
        "exclude_confusing": ft.Checkbox(label="排除易混字符 Il1O0o", value=bool(options["exclude_confusing"])),
    }
    length_slider = ft.Slider(min=6, max=64, divisions=58, value=int(options["length"]))

    def current() -> Dict[str, Any]:
        return {
            "length": int(length_slider.value),
            **{k: bool(v.value) for k, v in checks.items()},
        }

    def refresh(_e=None) -> None:
        length_label.value = str(int(length_slider.value))
        try:
            value = generator.generate_password(current())
            preview.value = value
            error.value = ""
            strength.value = f"强度：{crypto.password_strength(value)['label']}"
        except Exception as exc:
            preview.value = ""
            error.value = str(exc)
        page.update()

    strength = ft.Text("", size=13, color="#6b7889")
    length_slider.on_change = refresh
    for box in checks.values():
        box.on_change = refresh

    def use(_e=None) -> None:
        if not preview.value:
            return
        page.pop_dialog()
        on_use(preview.value)

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("密码生成器"),
        content=ft.Container(
            content=ft.Column([
                ft.Row([ft.Text("长度"), length_slider, length_label]),
                *checks.values(),
                ft.Text("预览", size=13, color="#6b7889"),
                ft.Container(content=preview, padding=10, border_radius=8, bgcolor="#f4f6fa",
                             border=ft.Border.all(1, "#d7dde7")),
                strength, error,
                ft.TextButton("重新生成", on_click=refresh),
            ], spacing=8, tight=True),
            width=330,
        ),
        actions=[ft.TextButton("取消", on_click=lambda _e: page.pop_dialog()),
                 ft.FilledButton("使用此密码", on_click=use)],
        modal=True,
    ))
    refresh()


# ---------------------------------------------------------------- 排序

def sort_dialog(page: ft.Page, current_key: str, current_desc: bool,
                on_apply: Callable[[str, bool], None]) -> None:
    """排序方式：点一项即时生效，方向在同一个弹层里切换，共两次点按。"""
    chosen = {"key": sorting.normalize_sort_key(current_key), "desc": bool(current_desc)}
    rows: List[Tuple[str, Any, ft.Container, ft.Icon]] = []

    def make_row(group: str, value: Any, label: str) -> ft.Container:
        tick = ft.Icon(getattr(ft.Icons, "CHECK", None) or ft.Icons.DONE, size=18,
                       color="#2f6fed", visible=False)
        box = ft.Container(
            content=ft.Row([ft.Text(label, expand=True), tick], spacing=6, tight=True),
            padding=ft.Padding.symmetric(horizontal=10, vertical=9),
            border_radius=8, bgcolor="#ffffff", border=ft.Border.all(1, "#e6ebf2"),
        )
        box.on_click = lambda _e, g=group, v=value: pick(g, v)
        rows.append((group, value, box, tick))
        return box

    def paint() -> None:
        for group, value, box, tick in rows:
            active = chosen[group] == value
            tick.visible = active
            box.bgcolor = "#eef4ff" if active else "#ffffff"
            box.border = ft.Border.all(1, "#2f6fed" if active else "#e6ebf2")

    def pick(group: str, value: Any) -> None:
        if chosen[group] == value:
            return
        chosen[group] = value
        paint()
        page.update()
        on_apply(chosen["key"], chosen["desc"])

    key_rows = [make_row("key", k, sorting.SORT_LABELS[k]) for k in sorting.SORT_KEYS]
    dir_rows = [make_row("desc", False, "递增（A→Z，旧→新）"),
                make_row("desc", True, "递减（Z→A，新→旧）")]
    paint()

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("排序方式"),
        content=ft.Container(
            content=ft.Column([
                ft.Text("先按什么排", size=13, color="#6b7889"),
                *key_rows,
                ft.Text("方向", size=13, color="#6b7889"),
                *dir_rows,
                ft.Text("改动立即生效，并会记住到下次打开。", size=12, color="#98a2b3"),
            ], spacing=6, tight=True),
            width=320,
        ),
        actions=[ft.FilledButton("完成", on_click=lambda _e: page.pop_dialog())],
        modal=True,
    ))


# ---------------------------------------------------------------- 加密同步

def sync_dialog(page: ft.Page, state, on_imported: Callable[[List[Credential], Dict[str, Any]], None]) -> None:
    passphrase = ft.TextField(label="同步口令（两端必须一致，至少 8 位）", password=True,
                              can_reveal_password=True, value=state.sync_passphrase or "")
    output = ft.TextField(label="导出：本机加密字符串", multiline=True, min_lines=4, max_lines=6, read_only=True)
    incoming = ft.TextField(label="导入：粘贴其它设备的字符串", multiline=True, min_lines=4, max_lines=6)
    meta = ft.Text("", size=12, color="#6b7889")
    error = ft.Text("", color="#c0392b", size=13)
    apply_button = ft.FilledButton("应用到本机", visible=False)
    pending: Dict[str, Any] = {}

    def guard() -> Optional[str]:
        text = passphrase.value or ""
        if len(text) < 8:
            error.value = "同步口令至少 8 位"
            page.update()
            return None
        state.sync_passphrase = text
        return text

    def do_pack(_e=None) -> None:
        key = guard()
        if not key:
            return
        vault = state.vault
        lines = sync.pack(vault.credentials, key, vault.header.revision, vault.header.device)
        output.value = "\n".join(lines)
        meta.value = f"已生成 {len(lines)} 片 · 本机版本 v{vault.header.revision} · 条目 {len(vault.credentials)} 条"
        error.value = ""
        page.update()

    def do_copy(_e=None) -> None:
        if not output.value:
            error.value = "请先生成"
            page.update()
            return
        copy_text(page, output.value, "同步字符串")

    def do_preview(_e=None) -> None:
        key = guard()
        if not key:
            return
        content = (incoming.value or "").strip()
        if not content:
            error.value = "请先粘贴同步字符串"
            page.update()
            return
        try:
            data = sync.unpack(content.splitlines(), key)
        except Exception as exc:
            error.value = f"解析失败：{exc}"
            meta.value = ""
            apply_button.visible = False
            page.update()
            return
        remote_rev = int(data.get("v", 0))
        local_rev = state.vault.header.revision
        decision = sync.resolve_conflict(local_rev, remote_rev, state.vault.header.device, str(data.get("device", "")))
        pending.clear()
        pending.update({"credentials": data["credentials"], "decision": decision, "revision": remote_rev})
        meta.value = (f"远端版本 v{remote_rev} · {len(data['credentials'])} 条 · "
                      f"本机 v{local_rev} · 判定：{decision}")
        error.value = ""
        apply_button.visible = True
        page.update()

    def do_apply(_e=None) -> None:
        if not pending:
            error.value = "请先解析预览"
            page.update()
            return

        def apply_now() -> None:
            creds = pending["credentials"]
            state.vault.credentials = list(creds)
            state.vault.save()
            page.pop_dialog()
            on_imported(creds, pending)

        if pending.get("decision") == "conflict":
            confirm_dialog(
                page, "同步冲突",
                f"本机与远端都已修改。继续将用远端 {len(pending['credentials'])} 条覆盖本机数据。\n"
                "建议先各自导出留档。确定继续吗？",
                on_confirm=apply_now, danger=True,
            )
            return
        apply_now()

    apply_button.on_click = do_apply
    page.show_dialog(ft.AlertDialog(
        title=ft.Text("加密同步"),
        content=ft.Container(
            content=ft.Column([
                passphrase,
                ft.Text("导出", size=13, color="#6b7889"),
                ft.Row([ft.FilledButton("生成上传字符串", on_click=do_pack),
                        ft.TextButton("复制全部", on_click=do_copy)]),
                output,
                ft.Text("导入", size=13, color="#6b7889"),
                incoming,
                ft.TextButton("解析预览", on_click=do_preview),
                meta, error,
            ], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO),
            width=350, height=470,
        ),
        actions=[apply_button, ft.TextButton("关闭", on_click=lambda _e: page.pop_dialog())],
        modal=True,
    ))


# ---------------------------------------------------------------- 设置

def settings_dialog(page: ft.Page, state, on_saved: Callable[[Dict[str, Any]], None],
                    on_change_master: Callable[[], None], on_rotate_recovery: Callable[[], None]) -> None:
    s = state.settings
    auto_lock = ft.TextField(label="自动锁定（分钟）", value=str(int(s.get("auto_lock_minutes", 5))), keyboard_type=ft.KeyboardType.NUMBER)
    clipboard = ft.TextField(label="剪贴板清空（秒）", value=str(int(s.get("clipboard_clear_seconds", 20))), keyboard_type=ft.KeyboardType.NUMBER)
    reveal = ft.TextField(label="密码回掩（秒）", value=str(int(s.get("reveal_seconds", 10))), keyboard_type=ft.KeyboardType.NUMBER)
    old_days = ft.TextField(label="密码过期提醒（天）", value=str(int(s.get("old_password_days", 180))), keyboard_type=ft.KeyboardType.NUMBER)
    error = ft.Text("", color="#c0392b", size=13)
    info = ft.Text("设置里不会有任何密码，只有这几项非敏感配置。", size=12, color="#98a2b3")

    def to_int(field: ft.TextField, default: int) -> int:
        try:
            return int(str(field.value).strip())
        except Exception:
            return default

    def save(_e=None) -> None:
        updated = dict(state.settings)
        updated.update({
            "auto_lock_minutes": max(0, to_int(auto_lock, 5)),
            "clipboard_clear_seconds": max(0, to_int(clipboard, 20)),
            "reveal_seconds": max(1, to_int(reveal, 10)),
            "old_password_days": max(1, to_int(old_days, 180)),
        })
        page.pop_dialog()
        on_saved(updated)

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("设置"),
        content=ft.Container(
            content=ft.Column([
                auto_lock, clipboard, reveal, old_days, info, error,
                ft.Divider(),
                ft.TextButton("修改主密码", on_click=lambda _e: (page.pop_dialog(), on_change_master())),
                ft.TextButton("重新生成恢复密钥", on_click=lambda _e: (page.pop_dialog(), on_rotate_recovery())),
            ], spacing=8, tight=True),
            width=330,
        ),
        actions=[ft.TextButton("取消", on_click=lambda _e: page.pop_dialog()),
                 ft.FilledButton("保存", on_click=save)],
        modal=True,
    ))


def change_master_dialog(page: ft.Page, state, on_done: Callable[[Optional[str]], None]) -> None:
    old = ft.TextField(label="原主密码", password=True, can_reveal_password=True)
    new = ft.TextField(label="新主密码（至少 8 位）", password=True, can_reveal_password=True)
    error = ft.Text("", color="#c0392b", size=13)

    def save(_e=None) -> None:
        if len(new.value or "") < 8:
            error.value = "新主密码至少 8 位"
            page.update()
            return
        try:
            state.vault.change_master_password(old.value or "", new.value or "")
        except Exception as exc:
            error.value = str(exc)
            page.update()
            return
        page.pop_dialog()
        on_done(state.vault.last_recovery_key)

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("修改主密码"),
        content=ft.Column([old, new, error,
                           ft.Text("修改后会同时生成新的恢复密钥。", size=12, color="#98a2b3")],
                          spacing=8, tight=True, width=320),
        actions=[ft.TextButton("取消", on_click=lambda _e: page.pop_dialog()),
                 ft.FilledButton("确定", on_click=save)],
        modal=True,
    ))
