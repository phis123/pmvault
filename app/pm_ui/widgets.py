"""可复用控件：掩码密码框、强度条、生成器弹窗、恢复密钥展示、确认框。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from pm_core import crypto, generator

MASK = "\u25cf"  # ●
STRENGTH_COLORS = ["#c0392b", "#e67e22", "#f1c40f", "#2ecc71", "#27ae60"]
MONO = ("Consolas", 11)


def center_on_parent(win: tk.Toplevel, parent, y_ratio: float = 0.34) -> None:
    """把弹窗摆到父窗口中央（纵向略偏上），并夹在屏幕内。

    背景：所有 tk.Toplevel 都没设过 geometry，系统默认会把新窗口丢到屏幕左上角，
    主窗口在屏幕中间时看着很别扭。统一在这里算位置。

    - 父窗口**已映射**（`winfo_viewable()`）且不小于弹窗：以父窗口为基准居中，纵向按 y_ratio 偏上
    - 其余情况（启动早期 / 父窗口 withdrawn / 父窗口比弹窗还小）：退化为按屏幕居中
    - 结果一律夹进屏幕

    必须在控件都建好之后调用（要用 winfo_reqwidth/reqheight 量真实尺寸）。

    踩坑记录：**不能用 `parent.winfo_width() <= 1` 判断"父窗口还没映射"**——
    Windows/Tk 8.6 下从未映射过的窗口自报 200x200@(34,57) 而不是 1x1，
    于是会走进父窗口分支、算出负偏移再被夹成 `+0+0`，正好是要消灭的左上角现象。
    可靠判据是 `winfo_viewable()`。（这个 bug 是子代理写测试时实测出来的。）
    """
    try:
        win.update_idletasks()
        w = max(win.winfo_reqwidth(), win.winfo_width())
        h = max(win.winfo_reqheight(), win.winfo_height())
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()

        px = py = None
        pw = ph = 0
        try:
            if parent is not None and parent.winfo_exists() and parent.winfo_viewable():
                px, py = parent.winfo_rootx(), parent.winfo_rooty()
                pw, ph = parent.winfo_width(), parent.winfo_height()
        except Exception:
            px = None

        if px is not None and pw >= w and ph >= h:
            x = px + (pw - w) // 2
            y = py + int((ph - h) * y_ratio)
        else:
            # 父窗口没映射或装不下弹窗 → 屏幕居中，绝不落到 +0+0
            x = (sw - w) // 2
            y = max(0, int((sh - h) * y_ratio))

        x = max(0, min(x, max(0, sw - w)))
        y = max(0, min(y, max(0, sh - h)))
        win.geometry(f"+{x}+{y}")
    except Exception:
        # 定位失败绝不能影响弹窗本身可用
        pass


class PasswordEntry(ttk.Frame):
    """带掩码、眼睛按钮、自动回掩的密码输入框。"""

    def __init__(self, master, width: int = 34, reveal_seconds: int = 10, readonly: bool = False) -> None:
        super().__init__(master)
        self.reveal_seconds = reveal_seconds
        self._revealed = False
        self._token = 0
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, show=MASK)
        self.entry.grid(row=0, column=0, sticky="we")
        self.eye = ttk.Button(self, text="显示", width=6, command=self.toggle)
        self.eye.grid(row=0, column=1, padx=(4, 0))
        if readonly:
            self.entry.state(["readonly"])
        self.columnconfigure(0, weight=1)

    # ------------------------------------------------------------ API
    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value)
        self.hide()

    def toggle(self) -> None:
        if self._revealed:
            self.hide()
        else:
            self.reveal()

    def reveal(self) -> None:
        self.entry.configure(show="")
        self.eye.configure(text="隐藏")
        self._revealed = True
        self._token += 1
        token = self._token
        self.after(self.reveal_seconds * 1000, lambda: self._auto_hide(token))

    def hide(self) -> None:
        self._token += 1
        self.entry.configure(show=MASK)
        self.eye.configure(text="显示")
        self._revealed = False

    def _auto_hide(self, token: int) -> None:
        if token == self._token and self._revealed:
            self.hide()

    def clear(self) -> None:
        self.set("")


class StrengthBar(ttk.Frame):
    """0–4 档强度条 + 文案。"""

    def __init__(self, master, label: str = "") -> None:
        super().__init__(master)
        self.prefix = label
        self.canvas = tk.Canvas(self, height=8, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="we")
        self.text = ttk.Label(self, width=26, anchor="w")
        self.text.grid(row=0, column=1, padx=(6, 0))
        self.columnconfigure(0, weight=1)
        self._size = (0, 0)

    def update_value(self, password: str) -> None:
        info = crypto.password_strength(password)
        score = int(info["score"])
        self.canvas.delete("all")
        self.update_idletasks()
        width = max(self.canvas.winfo_width(), 120)
        height = 8
        gap = 3
        seg = (width - gap * 4) / 5
        for i in range(5):
            x0 = i * (seg + gap)
            color = STRENGTH_COLORS[score] if i <= score else "#dcdcdc"
            self.canvas.create_rectangle(x0, 0, x0 + seg, height, fill=color, outline="")
        self.text.configure(text=f"{self.prefix}{info['label']}（熵 {info['entropy']} bit）")


def confirm(parent, title: str, message: str, danger: bool = False) -> bool:
    from tkinter import messagebox
    if danger:
        return bool(messagebox.askyesno(title, message, icon="warning", parent=parent))
    return bool(messagebox.askyesno(title, message, parent=parent))


def show_recovery_key(parent, key_text: str) -> bool:
    """显示一次性恢复密钥，用户必须勾选「我已抄写」才返回 True。"""
    win = tk.Toplevel(parent)
    win.title("请抄写恢复密钥")
    win.transient(parent)
    win.grab_set()
    win.resizable(False, False)

    ttk.Label(win, text="恢复密钥（只显示这一次）", font=("Microsoft YaHei", 12, "bold")).pack(padx=16, pady=(14, 4))
    ttk.Label(
        win,
        text="忘记主密码时，只能用它解锁。请抄写到纸上或存到离线位置，\n不要截图、不要发到聊天软件里。",
        justify="left",
    ).pack(padx=16, pady=(0, 8))

    box = tk.Text(win, height=2, width=42, font=("Consolas", 14), relief="solid", bd=1)
    box.insert("1.0", key_text)
    box.configure(state="disabled")
    box.pack(padx=16, pady=4)

    confirmed = tk.BooleanVar(value=False)
    ttk.Checkbutton(win, text="我已抄写并妥善保存", variable=confirmed).pack(padx=16, pady=(6, 4))

    result = {"ok": False}

    def done() -> None:
        if not confirmed.get():
            from tkinter import messagebox
            messagebox.showwarning("请先确认", "请先勾选「我已抄写并妥善保存」。", parent=win)
            return
        result["ok"] = True
        win.destroy()

    ttk.Button(win, text="完成", command=done).pack(padx=16, pady=(4, 14))
    center_on_parent(win, parent)
    win.wait_window()
    return result["ok"]


def generator_dialog(parent, initial: str = "") -> Optional[str]:
    """密码生成器：返回生成的密码，取消返回 None。"""
    win = tk.Toplevel(parent)
    win.title("生成密码")
    win.transient(parent)
    win.grab_set()
    win.resizable(False, False)

    opts: Dict[str, object] = dict(generator.DEFAULT_OPTIONS)
    length_var = tk.IntVar(value=int(opts["length"]))
    lower_var = tk.BooleanVar(value=bool(opts["lower"]))
    upper_var = tk.BooleanVar(value=bool(opts["upper"]))
    digits_var = tk.BooleanVar(value=bool(opts["digits"]))
    symbols_var = tk.BooleanVar(value=bool(opts["symbols"]))
    confusing_var = tk.BooleanVar(value=bool(opts["exclude_confusing"]))

    # ---- 先创建所有控件与回调会引用到的名字，最后再挂回调/触发刷新 ----
    # 教训：refresh() 会同时引用 preview 与 error。如果它们还没赋值就触发
    # （例如 Scale 的 command 在 scale.set() 时立刻回调），会抛
    # NameError: cannot access free variable 'error'（实测踩过两次）。
    preview = PasswordEntry(win, width=38)
    error = ttk.Label(win, text="", foreground="#c0392b")
    result: Dict[str, Optional[str]] = {"value": None}

    def current_options() -> Dict[str, object]:
        return {
            "length": int(length_var.get()),
            "lower": lower_var.get(),
            "upper": upper_var.get(),
            "digits": digits_var.get(),
            "symbols": symbols_var.get(),
            "exclude_confusing": confusing_var.get(),
        }

    def refresh() -> None:
        try:
            preview.set(generator.generate_password(current_options()))
            error.configure(text="")
        except Exception as exc:
            error.configure(text=str(exc))

    ttk.Label(win, text="长度").grid(row=0, column=0, sticky="w", padx=(16, 6), pady=(14, 4))
    length_label = ttk.Label(win, text=str(opts["length"]), width=3)
    length_label.grid(row=0, column=2, padx=(4, 16), pady=(14, 4))

    def on_scale(v: str) -> None:
        n = int(float(v))
        length_var.set(n)
        length_label.configure(text=str(n))
        refresh()

    scale = ttk.Scale(win, from_=6, to=64, orient="horizontal", length=240, command=on_scale)
    scale.grid(row=0, column=1, sticky="we", pady=(14, 4))

    checks = [
        ("小写字母 a-z", lower_var), ("大写字母 A-Z", upper_var), ("数字 0-9", digits_var),
        ("符号 !@#$", symbols_var), ("排除易混字符 Il1O0o", confusing_var),
    ]
    for i, (text, var) in enumerate(checks):
        ttk.Checkbutton(win, text=text, variable=var, command=refresh).grid(
            row=1 + i, column=0, columnspan=3, sticky="w", padx=16)

    ttk.Label(win, text="结果").grid(row=7, column=0, sticky="w", padx=(16, 6), pady=(10, 4))
    preview.grid(row=7, column=1, columnspan=2, sticky="we", padx=(0, 16), pady=(10, 4))
    error.grid(row=8, column=0, columnspan=3, sticky="w", padx=16)

    buttons = ttk.Frame(win)
    buttons.grid(row=9, column=0, columnspan=3, sticky="e", padx=16, pady=(8, 14))
    ttk.Button(buttons, text="重新生成", command=refresh).pack(side="left", padx=(0, 8))

    # 全部就绪后再设初始值并首次生成（此时 command 触发也是安全的）
    scale.set(int(opts["length"]))
    refresh()

    def use() -> None:
        value = preview.get()
        if not value:
            return
        result["value"] = value
        win.destroy()

    ttk.Button(buttons, text="使用", command=use).pack(side="left")
    ttk.Button(buttons, text="取消", command=win.destroy).pack(side="left", padx=(8, 0))

    if initial:
        preview.set(initial)
    refresh()
    center_on_parent(win, parent)
    win.wait_window()
    return result["value"]
