"""打包后的 Tkinter 版（方案 A）回归验证。

必须复制到 E:\\dsh\\work 之外运行（工作区有禁止运行 exe 的策略）。
覆盖：自检 --selftest、真启动并确认窗口出现且持续存活。
"""

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")

import tempfile


import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

BASE = _APP_DIR
SRC = os.path.join(os.path.dirname(_HERE), "dist_tk", "PMVaultTk")
TEST = os.path.join(_WORK_ROOT, "tk_exe")
FAILURES = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(label)


def window_titles(pid: int):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                found.append(buf.value)
        return True

    user32.EnumWindows(cb, 0)
    return found


def main() -> int:
    if not os.path.isdir(SRC):
        print(f"  [skip] 未找到打包产物：{SRC}")
        print("         先按 README 打包（PyInstaller --onedir），或跳过本测试")
        return 0
    if not os.path.isdir(SRC):
        print("找不到打包产物：", SRC)
        return 1

    shutil.rmtree(TEST, ignore_errors=True)
    shutil.copytree(SRC, TEST)
    exe = os.path.join(TEST, "PMVaultTk.exe")
    try:
        # 1. 自检（断言以程序自己写的 UTF-8 日志为准；控制台编码在冻结环境里不可控）
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([exe, "--selftest"], cwd=TEST, capture_output=True, timeout=120, env=env)
        check("自检退出码为 0", proc.returncode == 0, f"rc={proc.returncode} {proc.stderr.decode('utf-8','replace')[:200]}")

        log_path = os.path.join(TEST, "runtime.log")
        log_text = open(log_path, encoding="utf-8", errors="replace").read() if os.path.exists(log_path) else ""
        check("自检写入日志", bool(log_text), "runtime.log 为空")
        check("自检报告全部通过", "[selftest] 全部通过" in log_text, log_text[-200:])
        check("自检确认 Tk 可用", "Tk 可用" in log_text, log_text[:300])
        check("自检确认核心层可用", "核心层可用" in log_text, log_text[:300])
        check("自检确认界面可构建", "界面可构建" in log_text, log_text[:300])

        # 2. 真启动：窗口必须出现并持续存活
        proc = subprocess.Popen([exe], cwd=TEST, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        titles = []
        survived = 0
        for i in range(1, 13):
            time.sleep(1)
            if proc.poll() is not None:
                break
            survived = i
            if not titles:
                titles = window_titles(proc.pid)

        check("进程持续存活（12 秒）", survived >= 12, f"只活了 {survived} 秒，退出码={proc.poll()}")
        check("窗口真实出现", bool(titles), f"titles={titles}")
        check("窗口标题正确", any("账号密码管理" in t for t in titles), f"titles={titles}")

        log = os.path.join(TEST, "runtime.log")
        if os.path.exists(log):
            content = open(log, encoding="utf-8", errors="replace").read()
            check("日志记录进入 mainloop", "进入 mainloop" in content, content[-200:])
            check("日志中没有启动失败", "启动失败" not in content, content[-300:])

        check("没有 startup-error.log", not os.path.exists(os.path.join(TEST, "startup-error.log")))
        check("数据目录建在 exe 同目录", os.path.isdir(os.path.join(TEST, "data")))

        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        time.sleep(1)
    finally:
        shutil.rmtree(TEST, ignore_errors=True)

    print()
    if FAILURES:
        print(f"方案 A 打包产物验证失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("方案 A 打包产物验证全部通过（窗口真实出现并持续存活）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
