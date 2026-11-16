"""程序入口（方案 A：Tkinter 原生窗口版）。

特点：单进程、**不监听任何端口、不拉起子进程**，是目前最干净、最不易被安全软件干扰的形态。

用法：
    python run_tk.py              正常启动界面
    python run_tk.py --selftest   自检：建窗口、跑一遍核心层、立刻退出（用于打包后验证）
"""

from __future__ import annotations

import os
import sys
import traceback

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
for extra in (BASE, os.path.join(BASE, "libs")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

APP_TITLE = "账号密码管理"


def app_dir() -> str:
    """数据目录跟随 exe / 脚本所在目录（冻结后不能用 _MEIPASS，那是临时解包目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


LOG_PATH = os.path.join(app_dir(), "runtime.log")


def log(message: str) -> None:
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def selftest() -> int:
    """自检：确认 Tk 能建窗口、核心层能用。不需要人盯着界面。"""
    results = []
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        patchlevel = root.tk.eval("info patchlevel")
        tcl_lib = root.tk.eval("set tcl_library")
        root.destroy()
        results.append(f"Tk 可用，Tcl 补丁版本={patchlevel}，tcl_library={tcl_lib}")
    except Exception as exc:
        results.append(f"Tk 失败：{type(exc).__name__}: {exc}")
        log("自检失败：Tk\n" + traceback.format_exc())
        return 2

    try:
        import tempfile
        from pm_core import Credential, Vault
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vault.pmvault")
            vault = Vault.create(path, "Selftest-1!Strong")
            vault.add(Credential(name="GitHub", username="alice", password="pw-1", tags=["dev"]))
            again = Vault.unlock(path, "Selftest-1!Strong")
            raw = open(path, "rb").read()
            assert again.all()[0].password == "pw-1"
            assert b"GitHub" not in raw and b"pw-1" not in raw
        results.append("核心层可用：加解密往返正常、磁盘无明文")
    except Exception as exc:
        results.append(f"核心层失败：{type(exc).__name__}: {exc}")
        log("自检失败：核心层\n" + traceback.format_exc())
        return 3

    try:
        from pm_ui.app import App
        app = App()
        app.withdraw()
        title = app.title()
        app.destroy()
        results.append(f"界面可构建：标题={title}")
    except Exception as exc:
        results.append(f"界面失败：{type(exc).__name__}: {exc}")
        log("自检失败：界面\n" + traceback.format_exc())
        return 4

    for line in results:
        print("[selftest]", line, flush=True)
        log("[selftest] " + line)
    print("[selftest] 全部通过", flush=True)
    log("[selftest] 全部通过")
    return 0


def main() -> int:
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except Exception:
        pass

    if "--selftest" in sys.argv:
        code = selftest()
        return code

    log("=" * 60)
    log(f"启动 Tkinter 版  frozen={getattr(sys, 'frozen', False)}")
    log(f"python={sys.version.split()[0]}  executable={sys.executable}")
    log(f"app_dir={app_dir()}")
    try:
        from pm_ui.app import main as run_gui
        log("界面模块导入成功，进入 mainloop")
        run_gui()
        log("mainloop 正常结束")
        return 0
    except Exception:
        log("启动失败：\n" + traceback.format_exc())
        try:
            with open(os.path.join(app_dir(), "startup-error.log"), "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except Exception:
            pass
        raise


if __name__ == "__main__":
    sys.exit(main())
