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


# ---------------------------------------------------------------- 忘记密码 / 重置保险库
#
# 安全前提：不存在「绕过主密码」的合法途径。忘记主密码时只有两条路——
#   ① 用当初抄写的恢复密钥打开（recovery_key_dialog / Vault.unlock_with_recovery_key）；
#   ② 用户明确确认后删掉旧库重新开始（下面这个两步流程，旧数据不可恢复）。
# 所以第 1 步先把「两把钥匙」讲清楚，第 2 步再要求手打「重置」两个字做强制确认。

RESET_WORD = "重置"


def forget_password_dialog(page: ft.Page, on_continue: Callable, on_use_recovery: Callable) -> None:
    """忘记主密码 · 第 1 步：说明恢复密钥与主密码的关系，不带任何操作。"""
    def use_recovery(_e=None) -> None:
        page.pop_dialog()
        on_use_recovery()

    def go_on(_e=None) -> None:
        page.pop_dialog()
        on_continue()

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("忘记主密码？"),
        content=ft.Column([
            ft.Text("主密码与恢复密钥是打开保险库的两把钥匙。", size=13),
            ft.Text("如果当初抄写过「恢复密钥」（形如 ABCD-EFGH-IJKL-MNOP-QRST-UVWX），"
                    "现在仍然可以用它打开保险库，条目一条都不会丢。", size=13),
            ft.Text("两把钥匙都丢失时，数据在密码学上无法恢复：程序里没有后门，"
                    "也没人能替你算出主密码。", size=13, color="#7c5511"),
        ], spacing=10, tight=True, width=320),
        actions=[
            ft.TextButton("我还能找到恢复密钥", on_click=use_recovery),
            ft.FilledButton("继续", on_click=go_on),
        ],
        modal=True,
    ))


def reset_vault_dialog(page: ft.Page, on_reset: Callable) -> None:
    """忘记主密码 · 第 2 步：强确认删除旧保险库（不可恢复）。"""
    confirm = compact_field(f"请输入「{RESET_WORD}」确认")
    reset_button = ft.FilledButton("重置保险库", disabled=True)
    note = ft.Text(f"输入「{RESET_WORD}」两个字后，按钮才会变为可用。", size=12, color="#98a2b3")

    def on_type(_e=None) -> None:
        reset_button.disabled = (confirm.value or "").strip() != RESET_WORD
        page.update()

    confirm.on_change = on_type

    def do_reset(_e=None) -> None:
        if reset_button.disabled:
            return
        on_reset()          # 先执行重置，再收弹窗（界面随即回到「创建新保险库」状态）
        page.pop_dialog()

    reset_button.on_click = do_reset

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("重置保险库"),
        content=ft.Column([
            ft.Text("重置后旧保险库立即删除，其中的条目全部丢失。"
                    "重置只是让你能重新开始用这个 App。", size=13, color="#c0392b"),
            confirm,
            note,
        ], spacing=10, tight=True, width=320),
        actions=[
            ft.TextButton("取消", on_click=lambda _e: page.pop_dialog()),
            reset_button,
        ],
        modal=True,
    ))


# ---------------------------------------------------------------- 条目编辑
#
# 表单构建抽成 build_credential_form，供两处复用：
#   · screens/editor.py  —— 全屏编辑页（Android 上的实际入口）
#   · credential_dialog  —— 保留的弹窗版本（桌面预览 / 测试用）
# 这样字段样式与校验逻辑只有一份。

MUTED = "#6b7889"

# 紧凑字段尺寸：Android 默认 56dp 的输入框对 7 个字段的表单太占地方，
# 这里统一压到 46dp 并开 dense，让一屏能放下更多内容。
FIELD_HEIGHT = 46


def compact_field(label: str, *, hint: str = "", value: str = "", password: bool = False,
                  multiline: bool = False, min_lines: int = 2, max_lines: int = 4,
                  width: Optional[int] = None, autofocus: bool = False,
                  on_submit: Optional[Callable] = None, keyboard_type=None) -> ft.TextField:
    """移动端紧凑样式的输入框。"""
    field = ft.TextField(
        label=label,
        hint_text=hint or None,
        value=value,
        dense=True,
        text_size=14,
        label_style=ft.TextStyle(size=12),
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=8,
    )
    if multiline:
        field.multiline = True
        field.min_lines = min_lines
        field.max_lines = max_lines
    else:
        field.height = FIELD_HEIGHT
    if password:
        field.password = True
        field.can_reveal_password = True
    if width:
        field.width = width
    if autofocus:
        field.autofocus = True
    if on_submit:
        field.on_submit = on_submit
    if keyboard_type:
        field.keyboard_type = keyboard_type
    return field


def build_credential_form(page: ft.Page, defaults: Dict[str, Any], cred: Optional[Credential]):
    """构建新增/编辑表单。

    返回 (controls, collect, error, focus_first)：
      controls    —— 可直接放进页面的控件列表
      collect()   —— 校验并返回 Credential；失败返回 None 并写 error
      error       —— 错误提示 Text
      focus_first —— 首次渲染后应聚焦的字段（新增时聚焦名称，编辑时不聚焦）
    """
    target = cred or Credential()

    name = compact_field("名称 *", value=target.name, autofocus=not bool(cred))
    username = compact_field("用户名", value=target.username)
    password = compact_field("密码", value=target.password, password=True)
    url = compact_field("网址", value=target.url, keyboard_type=ft.KeyboardType.URL)
    tags = compact_field("标签", value=", ".join(target.tags), hint="用逗号分隔，例如：工作, 邮箱")
    notes = compact_field("备注", value=target.notes, multiline=True, min_lines=2, max_lines=4)
    error = ft.Text("", color="#c0392b", size=13)

    custom_rows: List[Dict[str, Any]] = []
    custom_box = ft.Column([], spacing=6)

    def add_custom(label: str = "", value: str = "", secret: bool = True) -> None:
        label_field = compact_field("字段名", value=label, width=118)
        value_field = compact_field("值", value=value, password=secret, width=None)
        value_field.expand = True

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
        generator_dialog(page, defaults,
                         on_use=lambda value: (setattr(password, "value", value), page.update()))

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

    controls = [
        name,
        username,
        ft.Row([password, ft.TextButton("生成", on_click=open_generator)]),
        url,
        tags,
        notes,
        ft.Row([ft.Text("自定义字段", size=13, color=MUTED),
                ft.TextButton("+ 添加", on_click=lambda _e: (add_custom(), page.update()))]),
        custom_box,
        error,
    ]
    return controls, collect, error, (name if not cred else None)


def credential_dialog(page: ft.Page, defaults: Dict[str, Any], cred: Optional[Credential],
                      on_save: Callable[[Credential], None], on_delete: Optional[Callable] = None) -> None:
    """弹窗版新增/编辑条目（保留：桌面预览与测试用；Android 上用 editor.py 的整页）。"""
    controls, collect, _error, _focus = build_credential_form(page, defaults, cred)

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
            content=ft.Column(controls, spacing=8, tight=True, scroll=ft.ScrollMode.AUTO),
            expand=True,
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
    passphrase = compact_field("同步口令（两端必须一致，至少 8 位）",
                               value=state.sync_passphrase or "", password=True)
    output = compact_field("导出：本机加密字符串", multiline=True, min_lines=4, max_lines=6)
    output.read_only = True
    incoming = compact_field("导入：粘贴其它设备的字符串", multiline=True, min_lines=4, max_lines=6)
    meta = ft.Text("", size=12, color="#6b7889")
    error = ft.Text("", color="#c0392b", size=13)
    apply_button = ft.FilledButton("应用到本机", visible=False)
    pending: Dict[str, Any] = {}

    # FilePicker 仅在宿主 page 提供 services（真实 Flet 页）时才可用；
    # 测试替身 / 不支持的环境里退化为字符串方式，不崩。
    picker = None
    services = getattr(page, "services", None)
    if services is not None:
        try:
            picker = ft.FilePicker()
            services.append(picker)
        except Exception:
            picker = None

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

    def parse_lines(lines: List[str], key: str) -> None:
        """字符串导入与文件导入共用的解析/冲突判定逻辑。"""
        try:
            data = sync.unpack(lines, key)
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

    def do_preview(_e=None) -> None:
        key = guard()
        if not key:
            return
        content = (incoming.value or "").strip()
        if not content:
            error.value = "请先粘贴同步字符串"
            page.update()
            return
        parse_lines(content.splitlines(), key)

    async def do_export_file() -> None:
        key = guard()
        if not key:
            return
        vault = state.vault
        try:
            text = sync.export_text(vault.credentials, key, vault.header.revision, vault.header.device)
            suggested = sync.default_filename(vault.header.revision, vault.header.device)
            # 用 ANY 而不是 CUSTOM+allowed_extensions：.pmvsync 是自定义扩展名，
            # 部分 Android ROM 上 CUSTOM 过滤映射不到 MIME 类型，会让选择器列不出文件。
            # 建议文件名本身已带 .pmvsync，所以 ANY 也能得到正确后缀。
            path = await picker.save_file(
                dialog_title="导出同步文件",
                file_name=suggested,
                file_type=ft.FilePickerFileType.ANY,
                src_bytes=text.encode("utf-8"),
            )
        except Exception as exc:
            error.value = f"导出失败：{exc}"
            meta.value = ""
            page.update()
            return
        if not path:
            # 用户取消，不打扰
            return
        meta.value = (f"已导出到：{path}\n"
                      f"用同一个同步口令在另一台设备「加密同步 → 从文件导入」即可。")
        error.value = ""
        page.update()

    async def do_import_file() -> None:
        key = guard()
        if not key:
            return
        try:
            # 同样用 ANY：这样也能选到被改名成 .txt 的同步文件，
            # 内容是不是同步包交给 looks_like_sync_file 判定。
            picked = await picker.pick_files(
                dialog_title="选择同步文件",
                file_type=ft.FilePickerFileType.ANY,
                allow_multiple=False,
            )
        except Exception as exc:
            error.value = f"选择文件失败：{exc}"
            page.update()
            return
        if not picked:
            # 用户取消
            return
        path = picked[0].path
        if not path:
            error.value = "无法获取所选文件路径，请重试"
            page.update()
            return
        try:
            lines = sync.load_lines(path)
        except Exception as exc:
            error.value = f"读取文件失败：{exc}"
            page.update()
            return
        joined = "\n".join(lines)
        if not sync.looks_like_sync_file(joined):
            error.value = "这个文件看起来不是 PMVault 同步文件（.pmvsync）"
            meta.value = ""
            apply_button.visible = False
            page.update()
            return
        parse_lines(lines, key)

    def start_file_task(handler) -> None:
        """FilePicker 缺失 / 环境不支持文件选择时退化为字符串方式并提示。"""
        if picker is None or getattr(page, "run_task", None) is None:
            error.value = "当前环境不支持文件选择，请改用「生成上传字符串」复制方式搬运。"
            page.update()
            return
        page.run_task(handler)

    def on_export_click(_e=None) -> None:
        start_file_task(do_export_file)

    def on_import_click(_e=None) -> None:
        start_file_task(do_import_file)

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
                ft.TextButton("导出到文件（.pmvsync）", on_click=on_export_click),
                output,
                ft.Text("导入", size=13, color="#6b7889"),
                incoming,
                ft.TextButton("解析预览", on_click=do_preview),
                ft.TextButton("从文件导入", on_click=on_import_click),
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
    auto_lock = compact_field("自动锁定（分钟）", value=str(int(s.get("auto_lock_minutes", 5))),
                              keyboard_type=ft.KeyboardType.NUMBER)
    clipboard = compact_field("剪贴板清空（秒）", value=str(int(s.get("clipboard_clear_seconds", 20))),
                              keyboard_type=ft.KeyboardType.NUMBER)
    reveal = compact_field("密码回掩（秒）", value=str(int(s.get("reveal_seconds", 10))),
                           keyboard_type=ft.KeyboardType.NUMBER)
    old_days = compact_field("密码过期提醒（天）", value=str(int(s.get("old_password_days", 180))),
                             keyboard_type=ft.KeyboardType.NUMBER)
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
    old = compact_field("原主密码", password=True)
    new = compact_field("新主密码（至少 8 位）", password=True)
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
