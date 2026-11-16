"""程序入口：启动本地服务并打开浏览器界面。

- 服务只监听 127.0.0.1（本机回环），端口随机。
- 数据文件放在本程序目录的 data\\ 下（冻结后是 exe 所在目录）。
- 全过程写 runtime.log，便于排查"双击后黑窗口一闪而过"这类问题。
- 控制台写入失败（编码问题）不会导致程序退出。
"""

from __future__ import annotations

import os
import sys
import time
import traceback

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
for extra in (BASE, os.path.join(BASE, "libs")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

APP_TITLE = "账号密码管理"


def _app_dir() -> str:
    """数据目录跟随 exe / 脚本所在目录（冻结后不能用 _MEIPASS，那是临时解包目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


LOG_PATH = os.path.join(_app_dir(), "runtime.log")


def log(message: str) -> None:
    """写日志；任何失败都不能影响程序运行。"""
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def say(message: str) -> None:
    """控制台输出；编码异常或没有控制台都不影响运行。"""
    try:
        print(message, flush=True)
    except Exception:
        try:
            print(message.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


def main() -> int:
    # 每次启动轮转一次日志，避免无限增长
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except Exception:
        pass

    log("=" * 60)
    log(f"启动 v2  frozen={getattr(sys, 'frozen', False)}")
    log(f"python={sys.version.split()[0]}  executable={sys.executable}")
    log(f"cwd={os.getcwd()}  app_dir={_app_dir()}")
    log(f"TEMP={os.environ.get('TEMP')}  TMP={os.environ.get('TMP')}")
    log(f"编码 stdout={getattr(sys.stdout, 'encoding', None)}")

    from pm_web import run as run_server

    data_dir = os.path.join(_app_dir(), "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        log("创建数据目录失败：" + traceback.format_exc())

    try:
        httpd, url = run_server(data_dir, logger=log)
        log(f"服务已启动：{url}  数据目录={data_dir}")
    except Exception:
        log("启动服务失败：\n" + traceback.format_exc())
        try:
            with open(os.path.join(_app_dir(), "startup-error.log"), "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except Exception:
            pass
        say("启动失败，详情见目录下的 runtime.log")
        return 1

    say("")
    say("=" * 56)
    say(f"  {APP_TITLE}")
    say(f"  界面地址：{url}")
    say(f"  数据目录：{data_dir}")
    say("  服务仅监听本机 127.0.0.1，不对外网开放。")
    say("  关闭本窗口（或按 Ctrl+C）即退出并清空内存中的密钥。")
    say("  如果浏览器没有自动打开，把上面的地址复制到浏览器地址栏即可。")
    say("=" * 56)
    say("")
    log("已打印地址，进入 serve_forever")

    # 浏览器万一没打开，给用户一个能手动打开界面的入口
    try:
        with open(os.path.join(_app_dir(), "打开界面.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"{APP_TITLE}\n\n把下面这一行复制到浏览器地址栏：\n{url}\n")
        log(f"已写入 打开界面.txt：{url}")
    except Exception:
        log("写 打开界面.txt 失败：" + traceback.format_exc())

    try:
        while True:
            try:
                httpd.serve_forever()
                log("serve_forever 正常返回（收到了 shutdown）")
                break
            except KeyboardInterrupt:
                say("正在退出…")
                log("收到 Ctrl+C")
                break
            except Exception:
                # 看门狗：服务循环异常时不要让窗口直接消失，记录后继续服务
                log("serve_forever 异常，5 秒后继续：\n" + traceback.format_exc())
                try:
                    time.sleep(5)
                except Exception:
                    break
    finally:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            log("收尾异常：" + traceback.format_exc())
        log("进程结束")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("顶层未捕获异常：\n" + traceback.format_exc())
        try:
            with open(os.path.join(_app_dir(), "startup-error.log"), "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except Exception:
            pass
        raise
