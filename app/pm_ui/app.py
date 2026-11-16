"""主窗口：解锁页 + 主面板 + 菜单（桌面端）。"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

import pm_core
from pm_core import Credential, Vault, crypto, sorting, sync

from . import dialogs, settings as settings_mod
from .helpers import AutoLock, ClipboardGuard
from .widgets import PasswordEntry, StrengthBar, center_on_parent, confirm, generator_dialog, show_recovery_key

APP_TITLE = "账号密码管理"
GEOMETRY = "1080x660"

# 路径：数据目录跟随 exe 所在位置（冻结后不能用 _MEIPASS，那是临时解包目录）
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")

DEFAULT_VAULT_NAME = os.path.join("data", "vault.pmvault")


def _is_inside(child: str, parent: str) -> bool:
    """判断 child 是否在 parent 目录内（统一大小写，避免 Windows 盘符大小写差异）。"""
    try:
        child_abs = os.path.normcase(os.path.abspath(child))
        parent_abs = os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath([child_abs, parent_abs]) == parent_abs
    except Exception:
        return False


def store_vault_path(path: str) -> str:
    """把保险库路径转成便于移植的写法。

    程序目录内的文件存**相对路径**（用 / 分隔），这样整目录拷到别的电脑、
    换盘符、换用户名都不会失效；目录外的文件仍存绝对路径。

    这是踩过的坑：之前一律存绝对路径，拷到别的电脑后程序会去找
    C:\\Users\\<原用户>\\... 这个不存在的路径，看起来就像"保险库丢了"。
    """
    path = os.path.abspath(path)
    if _is_inside(path, APP_DIR):
        rel = os.path.relpath(path, APP_DIR)
        return rel.replace("\\", "/")
    return path


def resolve_vault_path(stored: str) -> str:
    """把 settings 里的 vault_path 还原成绝对路径。"""
    if not stored:
        return os.path.join(DATA_DIR, "vault.pmvault")
    if os.path.isabs(stored):
        return stored
    return os.path.normpath(os.path.join(APP_DIR, stored))


class App(tk.Tk):
    def __init__(self, data_dir: Optional[str] = None) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(GEOMETRY)
        self._center_on_screen()
        self.minsize(900, 560)
        self.data_dir = data_dir or DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        self.settings_path = os.path.join(self.data_dir, "settings.json")

        self.settings = settings_mod.load(self.settings_path)
        stored = self.settings.get("vault_path") or ""
        resolved = resolve_vault_path(stored)
        # 兼容旧设置：以前存的是绝对路径，整目录搬到别的电脑后会指向不存在的位置。
        # 这时如果程序目录里的默认位置确实有保险库，就用默认位置（数据其实就在那儿）。
        default_path = os.path.join(DATA_DIR, "vault.pmvault")
        if stored and os.path.isabs(stored) and not os.path.exists(resolved) and os.path.exists(default_path):
            self.settings["vault_path"] = store_vault_path(default_path)
            settings_mod.save(self.settings_path, self.settings)
            resolved = default_path
        self.vault_path = resolved
        self.vault: Optional[Vault] = None
        self.clipboard = ClipboardGuard(self, int(self.settings.get("clipboard_clear_seconds", 20)))
        self.autolock = AutoLock(self, int(self.settings.get("auto_lock_minutes", 5)), self._auto_lock)
        self.selected_id: Optional[str] = None
        self.sync_passphrase_cache = ""

        self._build_menu()
        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)
        self.show_unlock()

    # ================================================================ 菜单
    def _center_on_screen(self) -> None:
        """主窗口居中显示。

        原来只给了尺寸（`1080x660`）没给位置，系统会把窗口丢到屏幕左上角；
        主窗口在左上角时，后面所有"相对主窗口居中"的弹窗也跟着偏，所以这里一起修。
        """
        try:
            width, height = (int(v) for v in GEOMETRY.lower().split("x"))
            screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 3)      # 略偏上，视觉更稳
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass                                   # 定位失败不影响启动

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新建保险库…", command=self._create_vault)
        file_menu.add_command(label="打开保险库…", command=self._open_vault_file)
        file_menu.add_separator()
        file_menu.add_command(label="导出加密副本…", command=self._export_copy)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)

        vault_menu = tk.Menu(menubar, tearoff=0)
        vault_menu.add_command(label="立即锁定", command=self.lock)
        vault_menu.add_command(label="修改主密码…", command=self._change_master)
        vault_menu.add_command(label="重新生成恢复密钥…", command=self._rotate_recovery)
        menubar.add_cascade(label="保险库", menu=vault_menu)

        tool_menu = tk.Menu(menubar, tearoff=0)
        tool_menu.add_command(label="密码生成器…", command=self._standalone_generator)
        tool_menu.add_command(label="加密同步…", command=self._open_sync)
        menubar.add_cascade(label="工具", menu=tool_menu)

        # 视图 → 排序方式（与安卓端同一套排序键，偏好写进 settings.json）
        self.sort_var_key = tk.StringVar(value=sorting.normalize_sort_key(self.settings.get("sort_key")))
        self.sort_var_desc = tk.BooleanVar(value=bool(self.settings.get("sort_desc", sorting.DEFAULT_SORT_DESC)))
        view_menu = tk.Menu(menubar, tearoff=0)
        sort_menu = tk.Menu(view_menu, tearoff=0)
        for key in sorting.SORT_KEYS:
            sort_menu.add_radiobutton(label=sorting.SORT_LABELS[key], value=key,
                                      variable=self.sort_var_key, command=self._on_sort_change)
        sort_menu.add_separator()
        sort_menu.add_radiobutton(label="递增（A→Z / 旧→新）", value=0,
                                  variable=self.sort_var_desc, command=self._on_sort_change)
        sort_menu.add_radiobutton(label="递减（Z→A / 新→旧）", value=1,
                                  variable=self.sort_var_desc, command=self._on_sort_change)
        view_menu.add_cascade(label="排序方式", menu=sort_menu)
        view_menu.add_separator()
        view_menu.add_command(label="刷新列表", command=self.refresh_list)
        menubar.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self._show_help)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.configure(menu=menubar)

    # ================================================================ 视图切换
    def _clear_container(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()
        self.selected_id = None

    def show_unlock(self) -> None:
        self.autolock.stop()
        self._clear_container()
        frame = ttk.Frame(self.container, padding=40)
        frame.pack(expand=True)

        ttk.Label(frame, text="\U0001f510  " + APP_TITLE, font=("Microsoft YaHei", 20, "bold")).pack(pady=(0, 4))
        exists = os.path.exists(self.vault_path)
        state = f"保险库：{self.vault_path}" + ("" if exists else "（尚未创建）")
        ttk.Label(frame, text=state, foreground="#666").pack(pady=(0, 18))

        ttk.Label(frame, text="主密码").pack(anchor="w")
        self.unlock_password = PasswordEntry(frame, width=36)
        self.unlock_password.pack(fill="x", pady=(2, 10))
        self.unlock_password.entry.bind("<Return>", lambda _e: self._do_unlock())

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(4, 6))
        ttk.Button(buttons, text="解锁" if exists else "创建并解锁", command=self._do_unlock if exists else self._create_vault).pack(side="left")
        if exists:
            ttk.Button(buttons, text="用恢复密钥解锁", command=self._unlock_with_recovery).pack(side="left", padx=(8, 0))
        else:
            ttk.Button(buttons, text="改路径…", command=self._choose_path).pack(side="left", padx=(8, 0))

        self.unlock_hint = ttk.Label(frame, text="提示：忘记主密码将无法恢复，请同时保存恢复密钥。", foreground="#c0392b")
        self.unlock_hint.pack(anchor="w", pady=(8, 0))

        # 忘记密码：只在已有保险库时出现，做成低调的文字链接（不是按钮，避免误点）
        if exists:
            forgot = tk.Label(frame, text="忘记密码？", foreground="#2f6fed", cursor="hand2",
                              font=("Microsoft YaHei", 9, "underline"))
            forgot.pack(anchor="w", pady=(10, 0))
            forgot.bind("<Button-1>", lambda _e: self._forgot_password())

        self.unlock_password.entry.focus_set()

    def show_main(self) -> None:
        assert self.vault is not None
        self._clear_container()
        self.autolock.start()

        toolbar = ttk.Frame(self.container, padding=(10, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="搜索").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.search_var, width=34)
        search.pack(side="left", padx=(6, 12))
        self.search_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Button(toolbar, text="+ 新增", command=self._add).pack(side="left")
        ttk.Button(toolbar, text="加密同步", command=self._open_sync).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="设置", command=self._open_settings).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="立即锁定", command=self.lock).pack(side="right")

        body = ttk.Panedwindow(self.container, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        left = ttk.Frame(body)
        columns = ("name", "username", "tags")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        for key, text, width in (("name", "名称", 220), ("username", "用户名", 170), ("tags", "标签", 120)):
            # 只有「名称」列可点（点一下切换升/降序）；排序统一走 pm_core.sorting，
            # 不再原地重排 vault.credentials（那会连带改掉存盘顺序）。
            if key == "name":
                self.tree.heading(key, text=text + " ⇅", command=self._toggle_name_sort)
            else:
                self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._edit())
        body.add(left, weight=3)

        self.detail = ttk.Frame(body, padding=14)
        body.add(self.detail, weight=2)

        self.status_var = tk.StringVar()
        status = ttk.Label(self.container, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 3))
        status.pack(fill="x", side="bottom")

        self.refresh_list()
        self._render_detail(None)
        search.focus_set()

        self.bind("<Control-f>", lambda _e: search.focus_set())
        self.bind("<Control-n>", lambda _e: self._add())
        self.bind("<Control-l>", lambda _e: self.lock())
        self.bind("<Escape>", lambda _e: self.search_var.set(""))

    # ================================================================ 列表与详情
    def _sort_key(self) -> str:
        return sorting.normalize_sort_key(self.settings.get("sort_key"))

    def _sort_desc(self) -> bool:
        return bool(self.settings.get("sort_desc", sorting.DEFAULT_SORT_DESC))

    def _set_sort(self, key: str, desc: bool) -> None:
        """记下排序方式（只写 settings.json，不碰加密库）并刷新。"""
        self.settings["sort_key"] = sorting.normalize_sort_key(key)
        self.settings["sort_desc"] = bool(desc)
        if hasattr(self, "sort_var_key"):
            self.sort_var_key.set(self.settings["sort_key"])
            self.sort_var_desc.set(self.settings["sort_desc"])
        try:
            settings_mod.save(self.settings_path, self.settings)
        except Exception:
            pass
        if self.vault is not None:
            self.refresh_list()

    def _on_sort_change(self) -> None:
        """菜单里的排序方式被改动。"""
        self._set_sort(self.sort_var_key.get(), bool(self.sort_var_desc.get()))

    def _toggle_name_sort(self) -> None:
        """点「名称」列头：在名称升序/降序之间切换。"""
        desc = self._sort_desc() if self._sort_key() == sorting.SORT_NAME else False
        self._set_sort(sorting.SORT_NAME, not desc)

    def refresh_list(self) -> None:
        if self.vault is None:
            return
        query = self.search_var.get() if hasattr(self, "search_var") else ""
        duplicates = {name for group in self.vault.duplicate_groups() for name in group}
        old_days = int(self.settings.get("old_password_days", 180))
        self.tree.delete(*self.tree.get_children())
        for cred in self.vault.search(query, sort_key=self._sort_key(), sort_desc=self._sort_desc()):
            mark = ""
            if cred.name in duplicates:
                mark += " \u26a0"      # 重复密码
            if cred.age_days() > old_days:
                mark += " \u23f0"      # 密码过期
            self.tree.insert("", "end", iid=cred.id,
                             values=(cred.name + mark, cred.username, ", ".join(cred.tags)))
        if self.selected_id and self.tree.exists(self.selected_id):
            self.tree.selection_set(self.selected_id)
        stats = self.vault.stats()
        self.status_var.set(
            f"已解锁 · 条目 {stats['count']} · 版本 v{stats['revision']} · KDF {stats['kdf']} · "
            f"弱密码 {stats['weak']} · 过期 {stats['old']} · "
            f"排序 {sorting.sort_label(self._sort_key(), self._sort_desc())} · "
            f"{self.autolock.remaining_text()}")

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self.selected_id = selection[0] if selection else None
        self._render_detail(self.vault.get(self.selected_id) if self.selected_id else None)

    def _render_detail(self, cred: Optional[Credential]) -> None:
        # 每次重绘都清掉旧的 StringVar 引用（见下面 field() 的说明）
        self._detail_vars: List[tk.StringVar] = []
        for child in self.detail.winfo_children():
            child.destroy()
        if cred is None:
            ttk.Label(self.detail, text="从左侧选择一个条目查看详情", foreground="#888").pack(anchor="w")
            return

        ttk.Label(self.detail, text=cred.name, font=("Microsoft YaHei", 14, "bold")).pack(anchor="w")
        if cred.url:
            ttk.Label(self.detail, text=cred.url, foreground="#2980b9").pack(anchor="w", pady=(0, 8))

        def field(label: str, value: str):
            row = ttk.Frame(self.detail)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=8, foreground="#555").pack(side="left", anchor="n")
            var = tk.StringVar(value=value or "—")
            # 必须保留强引用！StringVar 是局部变量时会被 Python 回收，
            # 回收时它从 Tcl 解释器注销 → 标签的 textvariable 指向不存在的变量 →
            # 该行显示为**空白**（标题在、值没了）。这正是"备注/用户名不显示"的根因。
            self._detail_vars.append(var)
            ttk.Label(row, textvariable=var, wraplength=320, justify="left").pack(side="left", fill="x", expand=True)
            return row, var

        field("用户名", cred.username)
        copy_user = ttk.Button(self.detail, text="复制用户名", command=lambda: self._copy(cred.username, "用户名"))
        copy_user.pack(anchor="w", pady=(2, 6))

        ttk.Label(self.detail, text="密码", foreground="#555").pack(anchor="w")
        pw = PasswordEntry(self.detail, width=32, reveal_seconds=int(self.settings.get("reveal_seconds", 10)), readonly=True)
        pw.pack(fill="x", pady=(2, 4))
        pw.set(cred.password)
        pw.eye.configure(command=lambda: self._toggle_reveal(pw))
        pw.entry.bind("<Button-1>", lambda _e: None)

        actions = ttk.Frame(self.detail)
        actions.pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="复制密码", command=lambda: self._copy(cred.password, "密码")).pack(side="left")
        ttk.Button(actions, text="编辑", command=self._edit).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="删除", command=self._delete).pack(side="left", padx=(8, 0))

        bar = StrengthBar(self.detail, label="强度：")
        bar.pack(fill="x", pady=(2, 6))
        bar.update_value(cred.password)

        if cred.tags:
            field("标签", ", ".join(cred.tags))
        if cred.notes:
            field("备注", cred.notes)
        if cred.custom_fields:
            ttk.Separator(self.detail).pack(fill="x", pady=8)
            for item in cred.custom_fields:
                wrapped = item.get("value", "")
                if item.get("secret"):
                    wrapped = "\u25cf" * min(len(wrapped), 12)
                field(item.get("label", "字段"), wrapped)

        warnings: List[str] = []
        if crypto.password_strength(cred.password)["score"] <= 1 and cred.password:
            warnings.append("密码强度偏弱")
        others = [c.name for c in self.vault.credentials if c.password and c.password == cred.password and c.id != cred.id]
        if others:
            warnings.append("与 " + "、".join(others) + " 使用相同密码")
        if cred.age_days() > int(self.settings.get("old_password_days", 180)):
            warnings.append(f"密码已使用 {cred.age_days()} 天，建议更换")
        if warnings:
            ttk.Label(self.detail, text="\n".join("\u26a0 " + w for w in warnings), foreground="#e67e22").pack(anchor="w", pady=(8, 0))

    def _toggle_reveal(self, widget: PasswordEntry) -> None:
        widget.toggle()
        if not widget._revealed:
            widget.set(widget.get())

    # ================================================================ 基本动作
    def _copy(self, text: str, what: str) -> None:
        if not text:
            return
        self.clipboard.copy(text)
        seconds = int(self.settings.get("clipboard_clear_seconds", 20))
        self.status_var.set(f"已复制{what}，{seconds} 秒后自动清空剪贴板")

    def _add(self) -> None:
        if self.vault is None:
            return
        cred = dialogs.edit_credential(self, self.settings.get("generator", {}))
        if cred:
            self.vault.add(cred)
            self.selected_id = cred.id
            self.refresh_list()
            self.status_var.set(f"已新增「{cred.name}」")

    def _edit(self) -> None:
        if self.vault is None or not self.selected_id:
            return
        cred = self.vault.get(self.selected_id)
        if cred is None:
            return
        updated = dialogs.edit_credential(self, self.settings.get("generator", {}), cred)
        if updated:
            self.vault.update(updated)
            self.refresh_list()
            self.status_var.set(f"已保存「{updated.name}」")

    def _delete(self) -> None:
        if self.vault is None or not self.selected_id:
            return
        cred = self.vault.get(self.selected_id)
        if cred is None:
            return
        if not confirm(self, "删除条目", f"确定要删除「{cred.name}」吗？\n此操作不可撤销（但可用 .bak 备份恢复）。", danger=True):
            return
        self.vault.delete(cred.id)
        self.selected_id = None
        self.refresh_list()
        self._render_detail(None)
        self.status_var.set(f"已删除「{cred.name}」")

    def _open_sync(self) -> None:
        if self.vault is None:
            messagebox.showinfo(APP_TITLE, "请先解锁保险库。", parent=self)
            return
        dialogs.open_sync_dialog(self, self.vault, self.sync_passphrase_cache)
        self.refresh_list()

    def _open_settings(self) -> None:
        updated = dialogs.settings_dialog(self, self.settings)
        if not updated:
            return
        self.settings = updated
        settings_mod.save(self.settings_path, self.settings)
        self.clipboard.clear_after_seconds = int(updated.get("clipboard_clear_seconds", 20))
        self.autolock.set_minutes(int(updated.get("auto_lock_minutes", 5)))
        self.refresh_list()
        self.status_var.set("设置已保存")

    def _standalone_generator(self) -> None:
        value = generator_dialog(self)
        if value:
            self.clipboard.copy(value)
            messagebox.showinfo(APP_TITLE, f"已生成并复制到剪贴板（{len(value)} 位）。", parent=self)

    # ================================================================ 保险库生命周期
    def _choose_path(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="选择保险库文件位置", defaultextension=".pmvault",
            initialdir=self.data_dir, initialfile="vault.pmvault",
            filetypes=[("密码保险库", "*.pmvault"), ("所有文件", "*.*")])
        if path:
            self.vault_path = os.path.abspath(path)
            self.settings["vault_path"] = store_vault_path(self.vault_path)
            settings_mod.save(self.settings_path, self.settings)
            self.show_unlock()

    def _open_vault_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="打开保险库", initialdir=self.data_dir,
            filetypes=[("密码保险库", "*.pmvault"), ("所有文件", "*.*")])
        if path:
            self.vault_path = os.path.abspath(path)
            self.settings["vault_path"] = store_vault_path(self.vault_path)
            settings_mod.save(self.settings_path, self.settings)
            self.show_unlock()

    def _ask_new_password(self, title: str) -> Optional[str]:
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text="主密码").grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        first = PasswordEntry(win, width=32)
        first.grid(row=0, column=1, padx=(0, 16), pady=(14, 4))
        ttk.Label(win, text="再输一次").grid(row=1, column=0, sticky="w", padx=16, pady=4)
        second = PasswordEntry(win, width=32)
        second.grid(row=1, column=1, padx=(0, 16), pady=4)
        info = ttk.Label(win, text="建议至少 12 位，混合大小写、数字与符号。", foreground="#777")
        info.grid(row=2, column=0, columnspan=2, sticky="w", padx=16)
        result: Dict[str, Optional[str]] = {"value": None}

        def ok() -> None:
            a, b = first.get(), second.get()
            if len(a) < 8:
                info.configure(text="主密码至少 8 位。", foreground="#c0392b")
                return
            if a != b:
                info.configure(text="两次输入不一致。", foreground="#c0392b")
                return
            strength = crypto.password_strength(a)
            if strength["score"] <= 1 and not confirm(win, "密码偏弱", f"当前强度：{strength['label']}。仍要使用吗？"):
                return
            result["value"] = a
            win.destroy()

        buttons = ttk.Frame(win)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", padx=16, pady=(10, 14))
        ttk.Button(buttons, text="确定", command=ok).pack(side="left")
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="left", padx=(8, 0))
        first.entry.focus_set()
        center_on_parent(win, self)
        self.wait_window(win)
        return result["value"]

    def _create_vault(self) -> None:
        if os.path.exists(self.vault_path):
            if not confirm(self, "覆盖已有保险库",
                           f"文件已存在：\n{self.vault_path}\n\n新建会覆盖它（原文件会先备份为 .bak）。继续吗？", danger=True):
                return
            os.replace(self.vault_path, self.vault_path + ".bak")
        password = self._ask_new_password("设置主密码")
        if not password:
            return
        try:
            self.vault = Vault.create(self.vault_path, password, device=self._device_id())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"创建失败：{exc}", parent=self)
            return
        self.show_main()
        recovery = self.vault.last_recovery_key
        if recovery:
            show_recovery_key(self, recovery)
        self.status_var.set("保险库已创建")

    def _do_unlock(self) -> None:
        password = self.unlock_password.get()
        if not password:
            self.unlock_hint.configure(text="请输入主密码。")
            return
        try:
            self.vault = Vault.unlock(self.vault_path, password)
        except crypto.WrongPasswordError:
            self.unlock_hint.configure(text="主密码错误。")
            return
        except crypto.TamperError:
            self.unlock_hint.configure(text="文件被篡改或损坏，已拒绝打开。")
            return
        except Exception as exc:
            self.unlock_hint.configure(text=f"打开失败：{exc}")
            return
        self.show_main()

    def _unlock_with_recovery(self) -> None:
        win = tk.Toplevel(self)
        win.title("用恢复密钥解锁")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text="恢复密钥（形如 ABCD-EFGH-…）").pack(padx=16, pady=(14, 4), anchor="w")
        var = tk.StringVar()
        ttk.Entry(win, textvariable=var, width=40, font=("Consolas", 12)).pack(padx=16)
        info = ttk.Label(win, text="", foreground="#c0392b")
        info.pack(padx=16, pady=(6, 0), anchor="w")
        done: Dict[str, bool] = {"ok": False}

        def go() -> None:
            try:
                self.vault = Vault.unlock_with_recovery_key(self.vault_path, var.get())
            except Exception as exc:
                info.configure(text=str(exc))
                return
            done["ok"] = True
            win.destroy()

        buttons = ttk.Frame(win)
        buttons.pack(padx=16, pady=(10, 14), anchor="e")
        ttk.Button(buttons, text="解锁", command=go).pack(side="left")
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="left", padx=(8, 0))
        center_on_parent(win, self)
        self.wait_window(win)
        if done["ok"] and self.vault is not None:
            self.show_main()
            messagebox.showinfo(APP_TITLE, "已用恢复密钥解锁。建议尽快修改主密码。", parent=self)

    def _change_master(self) -> None:
        if self.vault is None:
            return
        win = tk.Toplevel(self)
        win.title("修改主密码")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text="原主密码").grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        old = PasswordEntry(win, width=32)
        old.grid(row=0, column=1, padx=(0, 16), pady=(14, 4))
        ttk.Label(win, text="新主密码").grid(row=1, column=0, sticky="w", padx=16, pady=4)
        new = PasswordEntry(win, width=32)
        new.grid(row=1, column=1, padx=(0, 16), pady=4)
        info = ttk.Label(win, text="修改成功后旧密码立即失效，恢复密钥会重新生成。", foreground="#777")
        info.grid(row=2, column=0, columnspan=2, sticky="w", padx=16)

        def ok() -> None:
            if len(new.get()) < 8:
                info.configure(text="新主密码至少 8 位。", foreground="#c0392b")
                return
            try:
                self.vault.change_master_password(old.get(), new.get())
            except Exception as exc:
                info.configure(text=str(exc), foreground="#c0392b")
                return
            win.destroy()
            recovery = self.vault.last_recovery_key
            if recovery:
                show_recovery_key(self, recovery)
            messagebox.showinfo(APP_TITLE, "主密码已修改。", parent=self)
            self.refresh_list()

        buttons = ttk.Frame(win)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", padx=16, pady=(10, 14))
        ttk.Button(buttons, text="确定", command=ok).pack(side="left")
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="left", padx=(8, 0))
        center_on_parent(win, self)
        self.wait_window(win)

    def _rotate_recovery(self) -> None:
        if self.vault is None:
            return
        if not confirm(self, "重新生成恢复密钥", "旧恢复密钥会立即失效。确定继续吗？", danger=True):
            return
        key_text = self.vault.rotate_recovery_key()
        show_recovery_key(self, key_text)

    # ================================================================ 忘记密码（重置）
    #
    # 设计原则：
    #   1. 先给出路（恢复密钥），再谈重置 —— 不要让用户以为只能删。
    #   2. 明确写出"数据在密码学上无法恢复"，不给假希望。
    #   3. 重置前提供导出密文的选项，给用户留后路（想起密码时可救回）。
    #   4. 重置必须强确认（输入「重置」），防止手滑。
    #   5. 归档而不是直接删除：万一以后想起密码，那个文件还能打开。

    def _ask_text(self, title: str, prompt: str, expected: str) -> bool:
        """要求用户输入指定文字才能继续的强确认窗口。"""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(win, text=prompt, justify="left", wraplength=420).pack(padx=16, pady=(14, 8), anchor="w")
        var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=var, width=32)
        entry.pack(padx=16, anchor="w")
        hint = ttk.Label(win, text="", foreground="#c0392b")
        hint.pack(padx=16, pady=(6, 0), anchor="w")
        result = {"ok": False}

        enter_btn = ttk.Button(win, text="确定", state="disabled")
        cancel_btn = ttk.Button(win, text="取消", command=win.destroy)

        def on_change(*_a) -> None:
            enter_btn.configure(state="normal" if var.get().strip() == expected else "disabled")

        var.trace_add("write", on_change)

        def go() -> None:
            if var.get().strip() != expected:
                hint.configure(text=f"请输入「{expected}」以确认。")
                return
            result["ok"] = True
            win.destroy()

        enter_btn.configure(command=go)
        entry.bind("<Return>", lambda _e: go() if var.get().strip() == expected else None)

        row = ttk.Frame(win)
        row.pack(padx=16, pady=(10, 14), anchor="e")
        cancel_btn.pack(in_=row, side="left")
        enter_btn.pack(in_=row, side="left", padx=(8, 0))
        entry.focus_set()
        center_on_parent(win, self)
        self.wait_window(win)
        return result["ok"]

    def _forgot_password(self) -> None:
        """第 1 步：先给出路，再问是否继续。"""
        exists = os.path.exists(self.vault_path)
        extra = "" if exists else "\n\n提示：当前路径下没有找到保险库文件。"
        message = (
            "如果你抄写过「恢复密钥」，现在仍然可以打开保险库。\n"
            "恢复密钥形如：ABCD-EFGH-IJKL-MNOP-QRST-UVWX（6 组 4 位）\n\n"
            "主密码与恢复密钥是打开保险库的两把钥匙。\n"
            "两把都丢失时，里面的数据在密码学上无法恢复 —— 这不是程序限制，\n"
            "而是加密本身：程序里没有保存主密码，也没有任何「后门」。"
            + extra
        )
        if messagebox.askyesno(
                APP_TITLE,
                "忘记主密码？\n\n" + message + "\n\n你还能找到恢复密钥吗？\n"
                "（选「是」= 去用恢复密钥解锁；选「否」= 继续重置流程）",
                parent=self):
            self._unlock_with_recovery()
            return
        self._forgot_password_step2()

    def _forgot_password_step2(self) -> None:
        """第 2 步：问是否先导出密文留个后路。"""
        answer = messagebox.askyesnocancel(
            APP_TITLE,
            "重置前建议先导出加密数据。\n\n"
            "导出的是密文，需要原主密码才能打开；\n"
            "如果以后想起密码，用它就能把数据找回来。\n\n"
            "是否现在导出？\n"
            "（「是」= 选择保存位置并导出；「否」= 不导出直接重置；「取消」= 放弃重置）",
            parent=self)
        if answer is None:
            return
        if answer:
            self._export_for_reset()
            if not confirm(self, "继续重置",
                           "导出已完成。\n\n确定要继续重置保险库吗？\n"
                           "重置后旧保险库会归档保留，但在忘记密码的情况下无法再打开。",
                           danger=True):
                return
        self._forgot_password_step3()

    def _forgot_password_step3(self) -> None:
        """第 3 步：强确认后执行重置。"""
        prompt = (
            "重置保险库\n\n"
            "· 旧保险库会归档保留（文件仍在 data 目录，但忘记密码时打不开）\n"
            "· 你将可以重新创建新的保险库并设置新主密码\n"
            "· 旧保险库中的条目在新保险库里不会出现\n\n"
            "请输入「重置」两个字以确认："
        )
        if not self._ask_text("重置保险库", prompt, "重置"):
            return
        archived = self._archive_reset_vault()
        self.vault = None
        self.selected_id = None
        self.show_unlock()
        if archived:
            detail = "\n".join(os.path.basename(p) for p in archived)
            messagebox.showinfo(
                APP_TITLE,
                "已重置。\n\n旧文件已归档保留：\n" + detail +
                "\n\n它们仍是密文，只有想起原主密码才能打开。\n"
                "现在可以点击「创建并解锁」设置新的主密码。",
                parent=self)
        else:
            messagebox.showinfo(APP_TITLE, "已重置。现在可以点击「创建并解锁」设置新的主密码。", parent=self)

    def _archive_reset_vault(self) -> List[str]:
        """把保险库文件改名归档（不删除），返回归档后的路径列表。"""
        import datetime
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        archived: List[str] = []
        for suffix in ("", ".bak"):
            src = self.vault_path + suffix
            if not os.path.exists(src):
                continue
            target = src + f".{stamp}.旧"
            try:
                os.replace(src, target)
                archived.append(target)
            except Exception:
                # 改名失败也不能让流程卡死：退回删除（用户已强确认过）
                try:
                    os.remove(src)
                    archived.append(src + "（已删除，改名失败）")
                except Exception:
                    pass
        return archived

    def _export_for_reset(self) -> Optional[str]:
        """重置流程里的导出：把保险库密文（含 .bak 备份）另存到用户选的位置。"""
        import datetime
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        # 起始目录取保险库所在目录，而不是 self.data_dir（保险库可能在别的路径）
        start_dir = os.path.dirname(os.path.abspath(self.vault_path)) or self.data_dir
        target = filedialog.asksaveasfilename(
            parent=self, title="导出加密数据（重置前备份）",
            defaultextension=".pmvault",
            initialdir=start_dir,
            initialfile=f"vault-忘记密码前备份-{stamp}.pmvault",
            filetypes=[("密码保险库", "*.pmvault"), ("所有文件", "*.*")])
        if not target:
            return None
        try:
            import shutil as _shutil
            _shutil.copy2(self.vault_path, target)
            exported = [target]
            bak = self.vault_path + ".bak"
            if os.path.exists(bak):
                bak_target = target + ".bak"
                _shutil.copy2(bak, bak_target)
                exported.append(bak_target)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"导出失败：{exc}", parent=self)
            return None
        messagebox.showinfo(
            APP_TITLE,
            "已导出加密数据：\n" + "\n".join(exported) +
            "\n\n注意：这是**密文**，需要原主密码才能打开。\n"
            "请妥善保管；想起密码时，用桌面版「打开保险库…」选中它即可。",
            parent=self)
        return target

    def _export_copy(self) -> None:
        if self.vault is None:
            messagebox.showinfo(APP_TITLE, "请先解锁保险库。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="导出加密副本", defaultextension=".pmvault",
            initialdir=self.data_dir, initialfile="vault-backup.pmvault",
            filetypes=[("密码保险库", "*.pmvault")])
        if not path:
            return
        try:
            self.vault.export_encrypted(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"导出失败：{exc}", parent=self)
            return
        messagebox.showinfo(APP_TITLE, f"已导出加密副本：\n{path}\n\n仍用当前主密码解锁。", parent=self)

    def lock(self) -> None:
        if self.vault is not None:
            self.vault.lock()
            self.vault = None
        self.selected_id = None
        self.show_unlock()

    def _auto_lock(self) -> None:
        self.lock()
        messagebox.showinfo(APP_TITLE, "空闲超时，已自动锁定。", parent=self)

    def _device_id(self) -> str:
        import hashlib
        node = self.settings.get("device_id")
        if not node:
            node = "dev-" + hashlib.sha256(os.urandom(16)).hexdigest()[:8]
            self.settings["device_id"] = node
            settings_mod.save(self.settings_path, self.settings)
        return node

    # ================================================================ 帮助
    def _show_help(self) -> None:
        messagebox.showinfo(
            "使用说明",
            "1) 首次使用：点「创建并解锁」，设主密码，务必抄写恢复密钥。\n"
            "2) 日常：右上搜索框实时过滤；Ctrl+N 新增，Ctrl+F 搜索，Ctrl+L 锁定。\n"
            "3) 复制密码后 20 秒自动清空剪贴板（可在设置里改）。\n"
            "4) 加密同步：两台设备输入同一个同步口令，复制/粘贴那一串 PM1-… 即可互通。\n"
            "5) 忘记主密码时用恢复密钥解锁；两者都丢则数据无法恢复。\n"
            f"数据位置：{self.vault_path}",
            parent=self)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{APP_TITLE}\n\n"
            f"AES-256-GCM 加密 · Scrypt/Argon2id 派生 · 本地单文件存储\n"
            f"格式 {pm_core.FORMAT_NAME} v{pm_core.FORMAT_VERSION}\n"
            "不上传任何数据；同步只走密文字符串。",
            parent=self)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
