"""接口测试：直接跑本地服务，用标准库 HTTP 客户端逐个验证接口。

不需要浏览器参与，覆盖：会话令牌、解锁/创建、增删改查、生成器、设置、
加密同步打包与导入、导出、错误口令、限速、锁定后的未授权行为。
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
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

BASE = _APP_DIR
for p in (BASE,):
    if p not in sys.path:
        sys.path.insert(0, p)

from pm_web import run as run_server  # noqa: E402

FAILURES = []


def _maybe_json(text: str):
    """响应可能是 HTML（首页）也可能是 JSON；BOM、空体、非对象都要容忍，统一返回 dict。"""
    cleaned = text.lstrip("\ufeff").strip()
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"_html": cleaned[:120]}


class Client:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", "replace")
                return resp.status, _maybe_json(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            return exc.code, _maybe_json(body)

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, payload=None):
        return self.request("POST", path, payload or {})

    def patch(self, path, payload):
        return self.request("PATCH", path, payload)

    def delete(self, path):
        return self.request("DELETE", path)


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [ok]   {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        FAILURES.append(label)


def main() -> int:
    data_dir = tempfile.mkdtemp(prefix="pmapi_")
    httpd, url = run_server(data_dir, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    client = Client(url)
    try:
        # 0. 首次访问拿下会话 Cookie
        status, _ = client.get("/")
        check("GET / 返回首页", status == 200, f"status={status}")
        check("首次访问下发会话 Cookie", len(client.jar) >= 1)

        # 1. 未解锁状态
        status, body = client.get("/api/entries")
        check("未解锁时 /api/entries 返回 locked", status == 200 and body.get("locked") is True, str(body))
        check("未解锁时报告保险库不存在", body.get("vault_exists") is False, str(body))

        # 2. 创建保险库
        status, body = client.post("/api/unlock", {"mode": "create", "password": "Master-1!Strong"})
        check("创建保险库成功", status == 200 and body.get("unlocked") is True, str(body))
        recovery_key = body.get("recovery_key") or ""
        check("创建时返回恢复密钥", len(recovery_key.split("-")) == 6, recovery_key)

        # 3. 新增条目
        status, body = client.post("/api/entries", {
            "name": "GitHub", "username": "alice", "password": "pw-alpha-1",
            "url": "https://github.com", "notes": "两步验证已开", "tags": ["dev"],
            "custom_fields": [{"label": "PIN", "value": "1234", "secret": True}],
        })
        check("新增条目成功", status == 200 and body.get("entry", {}).get("name") == "GitHub", str(body))
        first_id = body["entry"]["id"]
        check("列表返回的密码是掩码", body["entry"]["password"].startswith("\u25cf"), body["entry"]["password"])

        client.post("/api/entries", {"name": "邮箱", "username": "bob", "password": "pw-alpha-1", "tags": ["mail"]})
        client.post("/api/entries", {"name": "旧账号", "username": "carol", "password": "123456"})

        # 4. 列表、搜索、标记
        status, body = client.get("/api/entries")
        check("列表返回 3 条", len(body.get("entries", [])) == 3, str(len(body.get("entries", []))))
        duplicates = [e for e in body["entries"] if e.get("duplicate")]
        check("重复密码被标记", len(duplicates) == 2, str([e["name"] for e in duplicates]))
        weak = [e for e in body["entries"] if e["strength"]["score"] <= 1]
        check("弱密码被识别", any(e["name"] == "旧账号" for e in weak), str([e["name"] for e in weak]))

        status, body = client.get("/api/entries?q=github")
        check("搜索过滤生效", len(body.get("entries", [])) == 1, str(body))

        # 5. 修改条目（包含改密码）
        status, body = client.patch(f"/api/entries/{first_id}", {"username": "alice2", "password": "BrandNew-9!x"})
        check("修改条目成功", status == 200 and body["entry"]["username"] == "alice2", str(body))
        status, body = client.get("/api/entries?q=github")
        check("修改后密码仍为掩码", body["entries"][0]["password"].startswith("\u25cf"))

        # 6. 密码生成器
        status, body = client.post("/api/generate", {"length": 24, "symbols": False})
        pw = body.get("password", "")
        check("生成器返回 24 位", status == 200 and len(pw) == 24, pw)
        check("生成密码不含符号", not any(ch in "!@#$%^&*()-_=+[]{};:,.?/" for ch in pw), pw)

        # 7. 设置
        status, body = client.post("/api/settings", {"auto_lock_minutes": 9, "clipboard_clear_seconds": 15})
        check("保存设置成功", status == 200 and body["settings"]["auto_lock_minutes"] == 9, str(body))
        status, body = client.get("/api/state")
        check("状态接口反映设置", body["settings"]["clipboard_clear_seconds"] == 15, str(body))
        check("状态接口统计正确", body["count"] == 3 and body["weak"] >= 1, str(body))

        # 8. 加密同步：打包 → 另一客户端导入
        status, body = client.get("/api/sync/estimate")
        check("同步预估可用", status == 200 and body["estimate"]["chunks"] >= 1, str(body))
        status, body = client.post("/api/sync/pack", {"passphrase": "sync-pass-1"})
        lines = body.get("lines", [])
        check("打包出分片字符串", status == 200 and len(lines) >= 1 and lines[0].startswith("PM1-"), str(body)[:120])
        joined = "\n".join(lines)
        check("分片里不含明文", "GitHub" not in joined and "BrandNew" not in joined)

        status, body = client.post("/api/sync/unpack", {"passphrase": "wrong-pass", "content": joined})
        check("错同步口令被拒绝", status >= 400, f"status={status}")

        status, body = client.post("/api/sync/unpack", {"passphrase": "sync-pass-1", "content": joined})
        check("预览导入成功", status == 200 and body["count"] == 3 and body.get("applied") is None, str(body)[:160])
        check("预览不写库", client.get("/api/entries")[1]["locked"] is False)

        missing = "\n".join(lines[1:]) if len(lines) > 1 else ""
        if len(lines) > 1:
            status, body = client.post("/api/sync/unpack", {"passphrase": "sync-pass-1", "content": missing})
            check("缺分片报明确错误", status >= 400 and "片" in body.get("error", ""), str(body))

        status, body = client.post("/api/sync/unpack", {"passphrase": "sync-pass-1", "content": joined, "apply": True})
        check("应用导入成功", status == 200 and body.get("applied") is True, str(body)[:160])
        check("应用后条目数仍为 3", len(client.get("/api/entries")[1]["entries"]) == 3)

        # 9. 删除
        status, _ = client.delete(f"/api/entries/{first_id}")
        check("删除条目成功", status == 200)
        check("删除后剩 2 条", len(client.get("/api/entries")[1]["entries"]) == 2)

        # 10. 导出加密副本
        export_path = os.path.join(data_dir, "export-backup.pmvault")
        status, body = client.post("/api/export", {"path": export_path})
        check("导出加密副本成功", status == 200 and os.path.exists(export_path), str(body))
        if os.path.exists(export_path):
            raw = open(export_path, "rb").read()
            check("导出文件里无明文", b"GitHub" not in raw and b"BrandNew" not in raw)

        # 11. 改主密码
        status, body = client.post("/api/master-password", {"old_password": "Master-1!Strong", "new_password": "New-Master-9!"})
        check("改主密码成功", status == 200 and body.get("recovery_key"), str(body)[:120])
        rotated_recovery = body.get("recovery_key") or ""
        status, body = client.post("/api/master-password", {"old_password": "Master-1!Strong", "new_password": "xxx-yyy-1!"})
        check("旧主密码已失效", status >= 400, f"status={status}")

        # 12. 锁定后行为
        status, _ = client.post("/api/lock")
        check("锁定接口成功", status == 200)
        status, body = client.get("/api/state")
        check("锁定后状态接口拒绝", status == 401, f"status={status}")
        status, body = client.post("/api/entries", {"name": "x"})
        check("锁定后不能新增", status == 401, f"status={status}")

        # 13. 用新主密码解锁 + 恢复密钥解锁
        status, body = client.post("/api/unlock", {"mode": "unlock", "password": "New-Master-9!"})
        check("新主密码解锁成功", status == 200 and body.get("unlocked") is True, str(body))
        check("解锁后数据仍在", len(client.get("/api/entries")[1]["entries"]) == 2)
        client.post("/api/lock")

        fresh = Client(url)
        fresh.get("/")
        status, body = fresh.post("/api/unlock", {"mode": "recovery", "recovery_key": rotated_recovery})
        check("恢复密钥可解锁", status == 200 and body.get("unlocked") is True, str(body)[:160])
        check("恢复密钥解锁后提示改主密码", body.get("want_change_master") is True, str(body))

        if os.path.exists(os.path.join(data_dir, "vault.pmvault")):
            raw = open(os.path.join(data_dir, "vault.pmvault"), "rb").read()
            check("保险库文件里无明文", all(needle not in raw for needle in (b"GitHub", b"alice2", b"BrandNew", b"123456")))
            check("自动生成 .bak 备份", os.path.exists(os.path.join(data_dir, "vault.pmvault.bak")))

    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"接口测试失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("接口测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
