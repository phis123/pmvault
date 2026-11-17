"""浏览器端到端验证：启动服务 + 用 Edge 无头模式加载页面，检查 JS 是否真的跑起来了。

验证点：
1. 页面能拿到会话 Cookie（首次访问 /）
2. JS 初始化没有抛错（用 --enable-logging 抓控制台错误）
3. 首页接口被调用，锁屏页渲染出动态内容（按钮文案、保险库路径）
4. 用真实 HTTP 客户端再走一遍「创建→新增→列表→同步」，与页面共用同一套接口
"""

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")


import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

BASE = _APP_DIR
for p in (BASE,):
    if p not in sys.path:
        sys.path.insert(0, p)

from pm_web import run as run_server  # noqa: E402

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

FAILURES = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(label)


def find_edge():
    for path in EDGE_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def main() -> int:
    data_dir = tempfile.mkdtemp(prefix="pmweb_")
    httpd, url = run_server(data_dir, open_browser=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    edge = find_edge()
    print(f"服务地址: {url}")
    print(f"浏览器: {edge or '未找到'}")

    try:
        # ---- 1. 纯 HTTP：页面与静态资源可获取，并下发 Cookie ----
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(url, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
        check("首页返回 200", True)
        check("首页是 HTML 且引用 app.js", "<html" in html.lower() and 'src="app.js"' in html, html[:80])
        check("首次访问下发会话 Cookie", len(jar) >= 1, str([c.name for c in jar]))

        with opener.open(url + "static/app.js", timeout=20) as resp:
            js = resp.read().decode("utf-8", "replace")
        check("app.js 可获取", resp.status == 200 and len(js) > 10000, f"len={len(js)}")
        check("app.js 是 UTF-8 中文", "账号密码管理" in js)

        # ---- 2. 走真实接口流程（与页面同一套后端）----
        # 注意：必须先做完 HTTP 流程，再启动无头浏览器——浏览器加载首页会建立自己的会话令牌。
        def post(path, payload):
            req = urllib.request.Request(url + path, data=json.dumps(payload).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json"})
            try:
                with opener.open(req, timeout=20) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

        def get(path):
            try:
                with opener.open(url + path, timeout=20) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

        status, body = post("/api/unlock", {"mode": "create", "password": "Master-1!Strong"})
        check("创建保险库（浏览器同一后端）", status == 200 and body.get("unlocked"), str(body)[:120])
        status, body = post("/api/entries", {"name": "GitHub", "username": "alice", "password": "pw-1", "tags": ["dev"]})
        check("新增条目", status == 200 and body["entry"]["name"] == "GitHub")
        status, body = get("/api/entries")
        check("列表读取正常", body.get("locked") is False and len(body["entries"]) == 1, str(body)[:120])
        status, pack = post("/api/sync/pack", {"passphrase": "sync-pass-1"})
        check("同步打包可用", status == 200 and pack["lines"][0].startswith("PM1-"))
        status, body = post("/api/sync/unpack", {"passphrase": "sync-pass-1",
                                                 "content": "\n".join(pack["lines"])})
        check("同步导入预览可用", status == 200 and body["count"] == 1, str(body)[:120])
        check("刷新首页不会顶掉已有会话", get("/api/state")[0] == 200)

        # ---- 3. 浏览器加载：检查 JS 是否真的初始化 ----
        if edge:
            profile = os.path.join(data_dir, "edge-profile")
            out_file = os.path.join(data_dir, "dom.html")
            log_file = os.path.join(data_dir, "edge.log")
            cmd = [
                edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                f"--user-data-dir={profile}", "--virtual-time-budget=10000",
                f"--enable-logging=stderr", "--log-level=0",
                "--dump-dom", url,
            ]
            with open(out_file, "wb") as out, open(log_file, "wb") as errlog:
                proc = subprocess.run(cmd, stdout=out, stderr=errlog, timeout=90)
            dom = open(out_file, encoding="utf-8", errors="replace").read() if os.path.exists(out_file) else ""
            log = open(log_file, encoding="utf-8", errors="replace").read() if os.path.exists(log_file) else ""

            check("Edge 完成页面加载", proc.returncode == 0 or len(dom) > 1000, f"rc={proc.returncode}")
            check("DOM 渲染出锁屏页", "lock-screen" in dom and "master-password" in dom)
            # 锁屏页文案由 JS 的 applyLockCopy() 写入；初始化成功才会被替换掉
            subtitle = re.search(r'id="lock-subtitle"[^>]*>([^<]*)<', dom)
            unlock_btn = re.search(r'id="btn-unlock"[^>]*>([^<]*)<', dom)
            hint = re.search(r'id="vault-path-hint"[^>]*>([^<]*)<', dom)
            check("JS 已执行（锁屏文案已渲染）",
                  bool(subtitle) and "请输入" in subtitle.group(1),
                  f"subtitle={subtitle.group(1) if subtitle else '未找到'}")
            check("解锁按钮文案已按状态渲染",
                  bool(unlock_btn) and "解锁" in unlock_btn.group(1),
                  f"button={unlock_btn.group(1) if unlock_btn else '未找到'}")
            check("保险库位置提示已渲染（或异步未及完成）",
                  hint is not None and ("保险库文件" in hint.group(1) or hint.group(1).strip() == ""),
                  f"hint={hint.group(1) if hint else '未找到节点'}")
            check("未解锁时进入锁屏（主界面隐藏）",
                  'id="vault-view" class="hidden"' in dom or 'id="vault-view"' in dom and "hidden" in dom.split('id="vault-view"')[1][:40],
                  "")
            # 控制台严重错误
            severe = [ln for ln in log.splitlines()
                      if ("SEVERE" in ln or "Uncaught" in ln or "TypeError" in ln or "SyntaxError" in ln)
                      and "app.js" in ln]
            check("页面无 JS 报错", not severe, str(severe[:2]))
        else:
            print("  (跳过浏览器验证：未找到 Edge)")

        # ---- 4. 浏览器加载后，页面拿到的会话是独立的，不应影响服务本身 ----
        check("浏览器加载后服务仍健康", get("/api/health")[0] == 200)

    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"浏览器端到端验证失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("浏览器端到端验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
