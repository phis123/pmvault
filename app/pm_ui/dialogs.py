"""各功能弹窗：条目编辑、设置、加密同步。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional

from pm_core import Credential, crypto, generator, sync

from . import settings as settings_mod
from .widgets import PasswordEntry, StrengthBar, center_on_parent, confirm, generator_dialog


# ---------------------------------------------------------------- 条目编辑

class CredentialDialog(tk.Toplevel):
    """新增 / 编辑条目。self.result 为 Credential 或 None。"""

    def __init__(self, parent, gen_defaults: Dict[str, Any], cred: Optional[Credential] = None) -> None:
        super().__init__(parent)
        self.title("编辑条目" if cred else "新增条目")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.result: Optional[Credential] = None
        self._cred = cred
        self._gen_defaults = gen_defaults

        self.name_var = tk.StringVar(value=cred.name if cred else "")
        self.user_var = tk.StringVar(value=cred.username if cred else "")
        self.url_var = tk.StringVar(value=cred.url if cred else "")
        self.tags_var = tk.StringVar(value=", ".join(cred.tags) if cred else "")

        row = 0
        ttk.Label(self, text="名称 *").grid(row=row, column=0, sticky="w", padx=(16, 8), pady=(14, 4))
        ttk.Entry(self, textvariable=self.name_var, width=40).grid(row=row, column=1, sticky="we", pady=(14, 4))
        row += 1

        ttk.Label(self, text="用户名").grid(row=row, column=0, sticky="w", padx=(16, 8), pady=4)
        ttk.Entry(self, textvariable=self.user_var, width=40).grid(row=row, column=1, sticky="we", pady=4)
        row += 1

        ttk.Label(self, text="密码").grid(row=row, column=0, sticky="w", padx=(16, 8), pady=4)
        self.password_entry = PasswordEntry(self, width=34)
        self.password_entry.grid(row=row, column=1, sticky="we", pady=4)
        if cred:
            self.password_entry.set(cred.password)
        ttk.Button(self, text="生成", width=6, command=self._generate).grid(row=row, column=2, padx=(6, 16), pady=4)
        row += 1

        self.strength = StrengthBar(self, label="强度：")
        self.strength.grid(row=row, column=1, sticky="we", pady=(0, 6))
        self.password_entry.var.trace_add("write", lambda *_: self.strength.update_value(self.password_entry.get()))
        row += 1

        ttk.Label(self, text="网址").grid(row=row, column=0, sticky="w", padx=(16, 8), pady=4)
        ttk.Entry(self, textvariable=self.url_var, width=40).grid(row=row, column=1, columnspan=2, sticky="we", padx=(0, 16), pady=4)
        row += 1

        ttk.Label(self, text="标签").grid(row=row, column=0, sticky="w", padx=(16, 8), pady=4)
        ttk.Entry(self, textvariable=self.tags_var, width=40).grid(row=row, column=1, columnspan=2, sticky="we", padx=(0, 16), pady=4)
        ttk.Label(self, text="用逗号分隔，例如：dev, work", foreground="#777").grid(row=row + 1, column=1, sticky="w")
        row += 2

        ttk.Label(self, text="备注").grid(row=row, column=0, sticky="nw", padx=(16, 8), pady=4)
        self.notes = tk.Text(self, height=4, width=40, wrap="word")
        self.notes.grid(row=row, column=1, columnspan=2, sticky="we", padx=(0, 16), pady=4)
        if cred and cred.notes:
            self.notes.insert("1.0", cred.notes)
        row += 1

        # 自定义字段
        ttk.Label(self, text="自定义字段").grid(row=row, column=0, sticky="nw", padx=(16, 8), pady=(8, 4))
        self.custom_frame = ttk.Frame(self)
        self.custom_frame.grid(row=row, column=1, columnspan=2, sticky="we", padx=(0, 16), pady=(8, 4))
        self._custom_rows: List[Dict[str, Any]] = []
        ttk.Button(self.custom_frame, text="+ 添加字段", command=lambda: self._add_custom_row()).grid(row=0, column=0, sticky="w")
        row += 1
        for field in (cred.custom_fields if cred else []) or []:
            self._add_custom_row(field.get("label", ""), field.get("value", ""), bool(field.get("secret", True)))

        self.error = ttk.Label(self, text="", foreground="#c0392b")
        self.error.grid(row=row, column=0, columnspan=3, sticky="w", padx=16)
        row += 1

        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", padx=16, pady=(6, 14))
        ttk.Button(buttons, text="保存", command=self._save).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=(8, 0))

        self.columnconfigure(1, weight=1)
        self.strength.update_value(self.password_entry.get())
        self.name_var.trace_add("write", lambda *_: self.error.configure(text=""))
        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        center_on_parent(self, parent)

    # ------------------------------------------------------------ 自定义字段
    def _add_custom_row(self, label: str = "", value: str = "", secret: bool = True) -> None:
        holder = ttk.Frame(self.custom_frame)
        holder.grid(row=len(self._custom_rows) + 1, column=0, sticky="we", pady=2)
        label_var = tk.StringVar(value=label)
        value_var = tk.StringVar(value=value)
        secret_var = tk.BooleanVar(value=secret)
        ttk.Entry(holder, textvariable=label_var, width=12).grid(row=0, column=0, padx=(0, 4))
        entry = ttk.Entry(holder, textvariable=value_var, width=20, show="\u25cf" if secret else "")
        entry.grid(row=0, column=1, padx=(0, 4))
        ttk.Checkbutton(holder, text="隐藏", variable=secret_var,
                        command=lambda: entry.configure(show="\u25cf" if secret_var.get() else "")).grid(row=0, column=2)
        record = {"label": label_var, "value": value_var, "secret": secret_var, "widget": holder}
        ttk.Button(holder, text="删除", width=6, command=lambda: self._remove_custom_row(record)).grid(row=0, column=3, padx=(4, 0))
        self._custom_rows.append(record)

    def _remove_custom_row(self, record: Dict[str, Any]) -> None:
        record["widget"].destroy()
        self._custom_rows.remove(record)

    # ------------------------------------------------------------ 动作
    def _generate(self) -> None:
        value = generator_dialog(self, self.password_entry.get())
        if value:
            self.password_entry.set(value)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            self.error.configure(text="名称不能为空")
            return
        cred = self._cred or Credential()
        cred.name = name
        cred.username = self.user_var.get().strip()
        cred.password = self.password_entry.get()
        cred.url = self.url_var.get().strip()
        cred.notes = self.notes.get("1.0", "end").strip()
        cred.tags = [t.strip() for t in self.tags_var.get().replace("，", ",").split(",") if t.strip()]
        cred.custom_fields = [
            {"label": r["label"].get().strip(), "value": r["value"].get(), "secret": bool(r["secret"].get())}
            for r in self._custom_rows if r["label"].get().strip()
        ]
        try:
            cred.validate()
        except Exception as exc:
            self.error.configure(text=str(exc))
            return
        self.result = cred
        self.destroy()


def edit_credential(parent, gen_defaults: Dict[str, Any], cred: Optional[Credential] = None) -> Optional[Credential]:
    dlg = CredentialDialog(parent, gen_defaults, cred)
    parent.wait_window(dlg)
    return dlg.result


# ---------------------------------------------------------------- 设置

def settings_dialog(parent, current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    win = tk.Toplevel(parent)
    win.title("设置")
    win.transient(parent)
    win.grab_set()
    win.resizable(False, False)
    result: Dict[str, Optional[Dict[str, Any]]] = {"value": None}

    lock_var = tk.IntVar(value=int(current.get("auto_lock_minutes", 5)))
    clip_var = tk.IntVar(value=int(current.get("clipboard_clear_seconds", 20)))
    reveal_var = tk.IntVar(value=int(current.get("reveal_seconds", 10)))
    old_var = tk.IntVar(value=int(current.get("old_password_days", 180)))

    rows = [
        ("自动锁定（分钟）", lock_var, 1, 120),
        ("剪贴板清空（秒）", clip_var, 5, 300),
        ("密码显示后回掩（秒）", reveal_var, 3, 120),
        ("密码过期提醒（天）", old_var, 30, 720),
    ]
    for i, (label, var, lo, hi) in enumerate(rows):
        ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=(16, 8), pady=6)
        ttk.Spinbox(win, from_=lo, to=hi, textvariable=var, width=8).grid(row=i, column=1, sticky="w", pady=6)

    hint = ttk.Label(win, text="设置文件为明文 JSON，只保存这些非敏感项。", foreground="#777")
    hint.grid(row=len(rows), column=0, columnspan=2, sticky="w", padx=16, pady=(2, 8))

    buttons = ttk.Frame(win)
    buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", padx=16, pady=(4, 14))

    def ok() -> None:
        updated = dict(current)
        updated.update({
            "auto_lock_minutes": int(lock_var.get()),
            "clipboard_clear_seconds": int(clip_var.get()),
            "reveal_seconds": int(reveal_var.get()),
            "old_password_days": int(old_var.get()),
        })
        result["value"] = updated
        win.destroy()

    ttk.Button(buttons, text="保存", command=ok).pack(side="left")
    ttk.Button(buttons, text="取消", command=win.destroy).pack(side="left", padx=(8, 0))
    center_on_parent(win, parent)
    win.wait_window()
    return result["value"]


# ---------------------------------------------------------------- 加密同步

def _default_dir() -> str:
    """导出/导入对话框的默认目录：优先桌面，其次文档，最后用户主目录。"""
    home = os.path.expanduser("~")
    for name in ("Desktop", "Documents", "桌面", "文档"):
        candidate = os.path.join(home, name)
        if os.path.isdir(candidate):
            return candidate
    return home


class SyncDialog(tk.Toplevel):
    """加密同步：**文件为主**，字符串方式保留作备选。

    改动原因（用户反馈）：整包同步字符串太长（几十片、几千字符），
    复制粘贴既容易缺片也不好搬运。现在主路径是「导出到文件…」——
    会弹系统另存为对话框，自己选存到哪（桌面/网盘目录/U 盘都行），
    再把这个文件传到另一台设备导入。
    """

    def __init__(self, parent, vault, default_passphrase: str = "") -> None:
        super().__init__(parent)
        self.title("加密同步")
        self.transient(parent)
        self.grab_set()
        self.resizable(True, True)
        self.vault = vault
        self.imported: Optional[List[Credential]] = None
        self._last_export: Optional[str] = None

        ttk.Label(self, text="同步口令（与主密码分开，两台设备输入同一个）").grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        self.pass_entry = PasswordEntry(self, width=40)
        self.pass_entry.grid(row=1, column=0, sticky="we", padx=16)
        if default_passphrase:
            self.pass_entry.set(default_passphrase)

        # ---- ① 导出（文件为主）
        ttk.Label(self, text="① 导出（本机 → 文件，推荐）").grid(
            row=2, column=0, sticky="w", padx=16, pady=(12, 4))
        export_bar = ttk.Frame(self)
        export_bar.grid(row=3, column=0, sticky="we", padx=16)
        ttk.Button(export_bar, text="导出到文件…", command=self._export_file).pack(side="left")
        ttk.Button(export_bar, text="生成字符串", command=self._generate).pack(side="left", padx=(8, 0))
        ttk.Button(export_bar, text="复制字符串", command=self._copy).pack(side="left", padx=(8, 0))
        self.open_dir_btn = ttk.Button(export_bar, text="打开所在文件夹",
                                       command=self._open_folder, state="disabled")
        self.open_dir_btn.pack(side="left", padx=(8, 0))

        # ---- ② 导入（文件为主）
        ttk.Label(self, text="② 导入（文件 → 本机）").grid(
            row=4, column=0, sticky="w", padx=16, pady=(12, 4))
        import_bar = ttk.Frame(self)
        import_bar.grid(row=5, column=0, sticky="we", padx=16)
        ttk.Button(import_bar, text="从文件导入…", command=self._import_file).pack(side="left")
        ttk.Button(import_bar, text="从下方字符串导入", command=self._import).pack(side="left", padx=(8, 0))

        ttk.Label(self, text="同步字符串（备选方式；导出/导入文件后这里也会显示内容）",
                  foreground="#777").grid(row=6, column=0, sticky="w", padx=16, pady=(12, 4))
        self.text = tk.Text(self, height=9, width=74, wrap="char", font=("Consolas", 9))
        self.text.grid(row=7, column=0, sticky="nsew", padx=16)

        self.status = ttk.Label(self, text="", foreground="#2c3e50", wraplength=580, justify="left")
        self.status.grid(row=8, column=0, sticky="w", padx=16, pady=(6, 0))

        self._bottom = ttk.Frame(self)
        self._bottom.grid(row=9, column=0, sticky="we", padx=16, pady=(8, 14))
        ttk.Button(self._bottom, text="关闭", command=self.destroy).pack(side="right")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(7, weight=1)
        self._refresh_status()
        center_on_parent(self, parent)

    # ------------------------------------------------------------ 状态
    def _refresh_status(self) -> None:
        info = sync.estimate(len(self.vault.credentials))
        self.status.configure(
            text=f"本机版本 v{self.vault.header.revision} · 条目 {len(self.vault.credentials)} 条 · 预计分片 {info['chunks']} 片",
            foreground="#2c3e50")

    def _need_passphrase(self) -> Optional[str]:
        passphrase = self.pass_entry.get()
        if len(passphrase) < 8:
            self.status.configure(text="同步口令至少 8 位", foreground="#c0392b")
            return None
        return passphrase

    # ------------------------------------------------------------ 导出
    def _export_file(self) -> None:
        passphrase = self._need_passphrase()
        if not passphrase:
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出加密同步文件（整篇密文，选存到哪）",
            defaultextension=sync.FILE_SUFFIX,
            initialfile=sync.default_filename(self.vault.header.revision, self.vault.header.device),
            initialdir=_default_dir(),
            filetypes=[("PMVault 同步文件", "*" + sync.FILE_SUFFIX), ("所有文件", "*.*")],
        )
        if not path:
            self.status.configure(text="已取消导出（未写入任何文件）", foreground="#e67e22")
            return
        try:
            saved = sync.save_to_file(path, self.vault.credentials, passphrase,
                                      self.vault.header.revision, self.vault.header.device)
        except Exception as exc:
            self.status.configure(text=f"导出失败：{exc}", foreground="#c0392b")
            return
        self._last_export = saved
        self.open_dir_btn.configure(state="normal")
        try:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", "\n".join(sync.load_lines(saved)))
        except Exception:
            pass
        self.status.configure(
            text=f"已导出到：{saved}\n"
                 f"（{len(self.vault.credentials)} 条）把这个文件传到另一台设备，"
                 "用同一个同步口令「从文件导入…」即可。口令忘了这个文件就永远打不开。",
            foreground="#27ae60")

    def _open_folder(self) -> None:
        if not self._last_export:
            return
        folder = os.path.dirname(self._last_export)
        try:
            if hasattr(os, "startfile"):
                os.startfile(folder)                      # noqa: S606  (Windows)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            self.status.configure(text=f"打开文件夹失败：{exc}", foreground="#c0392b")

    def _generate(self) -> None:
        passphrase = self._need_passphrase()
        if not passphrase:
            return
        lines = sync.pack(self.vault.credentials, passphrase, self.vault.header.revision, self.vault.header.device)
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.status.configure(text=f"已生成 {len(lines)} 片字符串，复制后请妥善保管；口令一旦忘记无法解密。",
                              foreground="#27ae60")

    def _copy(self) -> None:
        content = self.text.get("1.0", "end").strip()
        if not content:
            self.status.configure(text="内容为空", foreground="#c0392b")
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.status.configure(text=f"已复制 {len(content)} 字符到剪贴板", foreground="#27ae60")

    # ------------------------------------------------------------ 导入
    def _import_file(self) -> None:
        if not self._need_passphrase():
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="选择加密同步文件",
            initialdir=_default_dir(),
            filetypes=[("PMVault 同步文件", "*" + sync.FILE_SUFFIX),
                       ("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            self.status.configure(text="已取消导入（未改动本机数据）", foreground="#e67e22")
            return
        try:
            lines = sync.load_lines(path)
        except Exception as exc:
            self.status.configure(text=f"读取文件失败：{exc}", foreground="#c0392b")
            return
        if not sync.looks_like_sync_file("\n".join(lines)):
            if not confirm(self, "文件看起来不是同步包",
                           f"{os.path.basename(path)} 里没有找到 PM1- 开头的分片。\n仍要尝试解析吗？"):
                return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self._do_import(lines, f"文件 {os.path.basename(path)}")

    def _import(self) -> None:
        content = self.text.get("1.0", "end").strip()
        if not content:
            self.status.configure(text="请先粘贴同步字符串，或用「从文件导入…」", foreground="#c0392b")
            return
        self._do_import(content.splitlines(), "上方字符串")

    def _do_import(self, lines: List[str], source: str) -> None:
        passphrase = self._need_passphrase()
        if not passphrase:
            return
        try:
            data = sync.unpack(lines, passphrase)
        except sync.MissingChunksError as exc:
            self.status.configure(text=f"导入失败（{source}）：{exc}", foreground="#c0392b")
            return
        except Exception as exc:
            self.status.configure(text=f"导入失败（{source}）：{exc}", foreground="#c0392b")
            return

        remote_rev = int(data.get("v", 0))
        local_rev = self.vault.header.revision
        decision = sync.resolve_conflict(local_rev, remote_rev, self.vault.header.device, str(data.get("device", "")))
        creds: List[Credential] = data["credentials"]
        if decision == "conflict":
            if not confirm(self, "同步冲突",
                           f"本机版本 v{local_rev} 与远端版本 v{remote_rev} 都已修改。\n"
                           f"继续将用远端 {len(creds)} 条**覆盖**本机 {len(self.vault.credentials)} 条。\n"
                           "建议先分别导出留档。确定覆盖吗？", danger=True):
                self.status.configure(text="已取消导入（未改动本机数据）", foreground="#e67e22")
                return
        self.imported = creds
        self.status.configure(text=f"已从{source}解析 v{remote_rev}（{len(creds)} 条）。点击「应用到本机保险库」写入。",
                              foreground="#27ae60")
        self._apply_button()

    def _apply_button(self) -> None:
        if getattr(self, "_apply_btn", None):
            return
        self._apply_btn = ttk.Button(self._bottom, text="应用到本机保险库", command=self._apply)
        self._apply_btn.pack(side="left")

    def _apply(self) -> None:
        if self.imported is None:
            return
        self.vault.credentials = list(self.imported)
        self.vault.save()
        self.status.configure(text=f"已写入本机，当前 {len(self.vault.credentials)} 条", foreground="#27ae60")
        self._refresh_status()


def open_sync_dialog(parent, vault, default_passphrase: str = "") -> Optional[List[Credential]]:
    dlg = SyncDialog(parent, vault, default_passphrase)
    parent.wait_window(dlg)
    return dlg.imported
