"""本地服务：仅监听 127.0.0.1，为浏览器界面提供 JSON 接口。

设计约束：
- 只绑定回环地址，端口随机；不对外网暴露。
- 首次 GET / 下发一个一次性会话令牌（HttpOnly Cookie），之后所有 /api/* 必须携带。
- 主密码只用于派生密钥，绝不落盘；锁定后内存中的条目与密钥一并清空。
- 静态资源内嵌在 pm_web/static/ 下，打包时随程序走。
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from pm_core import Credential, Vault, crypto, generator, sync

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE_NAME = "pm_session"
MAX_BODY = 32 * 1024 * 1024          # 同步字符串可能较长，给到 32 MB
LOCKOUT_AFTER = 5                     # 连续失败几次开始退避
SESSION_TTL_SECONDS = 12 * 3600


class Session:
    """进程内状态：令牌、保险库、设置、锁屏计时。"""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.token: Optional[str] = None
        self.vault: Optional[Vault] = None
        self.vault_path = os.path.join(data_dir, "vault.pmvault")
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.settings: Dict[str, Any] = _load_settings(self.settings_path)
        self.failures = 0
        self.locked_until = 0.0
        self.last_activity = time.time()
        self.sync_passphrase = ""
        self.recovery_key_once: Optional[str] = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------ 会话
    def issue_token(self) -> str:
        self.token = secrets.token_urlsafe(32)
        return self.token

    def check_token(self, presented: Optional[str]) -> bool:
        return bool(self.token) and bool(presented) and secrets.compare_digest(self.token, presented)

    def touch(self) -> None:
        self.last_activity = time.time()

    def idle_seconds(self) -> float:
        return time.time() - self.last_activity

    def autolock_seconds(self) -> int:
        return max(1, int(self.settings.get("auto_lock_minutes", 5))) * 60

    def maybe_autolock(self) -> bool:
        if self.vault is not None and self.idle_seconds() > self.autolock_seconds():
            self.lock_vault()
            return True
        return False

    def lock_vault(self) -> None:
        if self.vault is not None:
            self.vault.lock()
        self.vault = None

    # ------------------------------------------------------------ 解锁限速
    def register_failure(self) -> None:
        self.failures += 1
        if self.failures >= LOCKOUT_AFTER:
            self.locked_until = time.time() + min(2 ** (self.failures - LOCKOUT_AFTER + 1), 30)

    def register_success(self) -> None:
        self.failures = 0
        self.locked_until = 0.0

    def cooldown_remaining(self) -> float:
        return max(0.0, self.locked_until - time.time())

    # ------------------------------------------------------------ 设置
    def save_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"auto_lock_minutes", "clipboard_clear_seconds", "reveal_seconds", "old_password_days", "generator"}
        for key, value in (patch or {}).items():
            if key in allowed:
                self.settings[key] = value
        _save_settings(self.settings_path, self.settings)
        return self.settings


def _load_settings(path: str) -> Dict[str, Any]:
    from pm_ui import settings as settings_mod  # 复用同一份默认值定义
    return settings_mod.load(path)


def _save_settings(path: str, settings: Dict[str, Any]) -> None:
    from pm_ui import settings as settings_mod
    settings_mod.save(path, settings)


class Handler(BaseHTTPRequestHandler):
    server_version = "PmVault/1.0"
    protocol_version = "HTTP/1.1"
    session: Session = None  # type: ignore[assignment]

    # ------------------------------------------------------------ 基础
    def log_message(self, fmt: str, *args: Any) -> None:  # 不把访问日志写到控制台之外
        if os.environ.get("PM_DEBUG"):
            sys.stderr.write("[pm] " + (fmt % args) + "\n")

    def _cookie_token(self) -> Optional[str]:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _authed(self) -> bool:
        return self.session.check_token(self._cookie_token())

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8",
              extra_headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), extra_headers=extra_headers)

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------ 路由
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_index()
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        if path == "/api/health":
            self._json(200, {"ok": True})   # 存活探测不需要鉴权
            return
        if not self._authed():
            self._json(401, {"error": "未授权的会话"})
            return
        self.session.touch()
        handler = {
            "/api/state": self.api_state,
            "/api/entries": self.api_list,
            "/api/sync/estimate": self.api_sync_estimate,
        }.get(path)
        if handler is None:
            self._json(404, {"error": "未知接口"})
            return
        try:
            handler()
        except Exception as exc:  # pragma: no cover - 兜底
            self._json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/session" and self._cookie_token() is None:
            self._start_session()
            return
        if not self._authed():
            self._json(401, {"error": "未授权的会话"})
            return
        self.session.touch()
        routes = {
            "/api/unlock": self.api_unlock,
            "/api/lock": self.api_lock,
            "/api/entries": self.api_create,
            "/api/generate": self.api_generate,
            "/api/master-password": self.api_change_master,
            "/api/recovery/rotate": self.api_rotate_recovery,
            "/api/settings": self.api_settings,
            "/api/sync/pack": self.api_sync_pack,
            "/api/sync/unpack": self.api_sync_unpack,
            "/api/export": self.api_export,
        }
        handler = routes.get(path)
        if handler is None:
            self._json(404, {"error": "未知接口"})
            return
        try:
            handler()
        except crypto.WrongPasswordError as exc:
            self.session.register_failure()
            self._json(403, {"error": str(exc), "cooldown": self.session.cooldown_remaining()})
        except crypto.TamperError as exc:
            self._json(409, {"error": str(exc)})
        except Exception as exc:
            self._json(400, {"error": str(exc)})

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._authed():
            self._json(401, {"error": "未授权的会话"})
            return
        self.session.touch()
        path = urlparse(self.path).path
        if path.startswith("/api/entries/"):
            try:
                self.api_update(path.rsplit("/", 1)[-1])
            except Exception as exc:
                self._json(400, {"error": str(exc)})
            return
        self._json(404, {"error": "未知接口"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authed():
            self._json(401, {"error": "未授权的会话"})
            return
        self.session.touch()
        path = urlparse(self.path).path
        if path.startswith("/api/entries/"):
            try:
                self.api_delete(path.rsplit("/", 1)[-1])
            except Exception as exc:
                self._json(400, {"error": str(exc)})
            return
        self._json(404, {"error": "未知接口"})

    # ------------------------------------------------------------ 静态资源
    def _serve_index(self) -> None:
        # 只有「还没有会话」或「带来的令牌无效」时才新签发，避免刷新页面把已有会话顶掉
        if not self._authed():
            token = self.session.issue_token()
            self._send_file(
                os.path.join(STATIC_DIR, "index.html"),
                extra_headers={"Set-Cookie": f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/"},
            )
            return
        self._send_file(os.path.join(STATIC_DIR, "index.html"))

    def _serve_static(self, relative: str) -> None:
        safe = os.path.normpath(relative).replace("\\", "/").lstrip("/")
        if safe.startswith("..") or os.path.isabs(safe):
            self._json(403, {"error": "非法路径"})
            return
        self._send_file(os.path.join(STATIC_DIR, safe))

    def _send_file(self, path: str, extra_headers: Optional[Dict[str, str]] = None) -> None:
        if not os.path.isfile(path):
            self._json(404, {"error": "资源不存在"})
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as fh:
            body = fh.read()
        self._send(200, body, content_type=ctype, extra_headers=extra_headers)

    def _start_session(self) -> None:
        token = self.session.issue_token()
        self._json(200, {"ok": True}, extra_headers={
            "Set-Cookie": f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/"})


def _device_id(session: Session) -> str:
    device = session.settings.get("device_id")
    if not device:
        import hashlib
        device = "dev-" + hashlib.sha256(os.urandom(16)).hexdigest()[:8]
        session.settings["device_id"] = device
        _save_settings(session.settings_path, session.settings)
    return device


def _credential_payload(cred: Credential, reveal: bool = False) -> Dict[str, Any]:
    return {
        "id": cred.id,
        "name": cred.name,
        "username": cred.username,
        "password": cred.password if reveal else "\u25cf" * min(len(cred.password), 14),
        "url": cred.url,
        "notes": cred.notes,
        "tags": cred.tags,
        "custom_fields": cred.custom_fields,
        "created_at": cred.created_at,
        "updated_at": cred.updated_at,
        "password_updated_at": cred.password_updated_at,
        "age_days": cred.age_days(),
        "strength": crypto.password_strength(cred.password),
    }


def _install_api(session: Session) -> None:
    """把各接口实现挂到 Handler 上（保持文件可读性）。"""

    def require_vault(func):
        def wrapper(self: Handler, *args, **kwargs):
            if self.session.vault is None:
                self._json(401, {"error": "保险库未解锁", "locked": True})
                return
            if self.session.maybe_autolock():
                self._json(401, {"error": "空闲超时，已自动锁定", "locked": True})
                return
            return func(self, *args, **kwargs)
        return wrapper

    @require_vault
    def _api_state(self: Handler) -> None:
        vault = self.session.vault
        self._json(200, {
            "unlocked": True,
            "count": len(vault.credentials),
            "revision": vault.header.revision,
            "device": vault.header.device,
            "updated_at": vault.header.updated_at,
            "kdf": vault.header.kdf.get("algo"),
            "weak": sum(1 for c in vault.credentials if crypto.password_strength(c.password)["score"] <= 1),
            "old": sum(1 for c in vault.credentials if c.age_days() > int(self.session.settings.get("old_password_days", 180))),
            "settings": self.session.settings,
        })

    def _api_list(self: Handler) -> None:
        vault = self.session.vault
        if vault is None:
            # 未解锁时也允许查询「是否已有保险库」，方便首屏
            self._json(200, {"locked": True, "vault_exists": os.path.exists(self.session.vault_path),
                             "vault_path": self.session.vault_path})
            return
        query = ""
        if "?" in self.path:
            from urllib.parse import parse_qs
            query = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
        duplicates = {name for group in vault.duplicate_groups() for name in group}
        items = [_credential_payload(c) for c in vault.search(query)]
        for item in items:
            item["duplicate"] = item["name"] in duplicates
            item["old"] = item["age_days"] > int(self.session.settings.get("old_password_days", 180))
        self._json(200, {"locked": False, "entries": items})

    def _api_unlock(self: Handler) -> None:
        body = self._read_json()
        if self.session.cooldown_remaining() > 0:
            self._json(429, {"error": f"尝试过于频繁，请等待 {int(self.session.cooldown_remaining())} 秒",
                             "cooldown": self.session.cooldown_remaining()})
            return
        mode = body.get("mode", "unlock")
        password = body.get("password") or ""
        if mode == "create":
            if len(password) < 8:
                self._json(400, {"error": "主密码至少 8 位"})
                return
            if os.path.exists(self.session.vault_path):
                if not body.get("overwrite"):
                    self._json(409, {"error": "保险库已存在，需要确认覆盖"})
                    return
                os.replace(self.session.vault_path, self.session.vault_path + ".bak")
            vault = Vault.create(self.session.vault_path, password, device=_device_id(self.session))
            self.session.vault = vault
            self.session.register_success()
            self.session.recovery_key_once = vault.last_recovery_key
            self._json(200, {"unlocked": True, "recovery_key": vault.last_recovery_key})
            return
        if mode == "recovery":
            vault = Vault.unlock_with_recovery_key(self.session.vault_path, body.get("recovery_key") or "")
        else:
            vault = Vault.unlock(self.session.vault_path, password)
        self.session.vault = vault
        self.session.register_success()
        self._json(200, {"unlocked": True, "count": len(vault.credentials),
                         "want_change_master": mode == "recovery"})

    def _api_lock(self: Handler) -> None:
        self.session.lock_vault()
        self._json(200, {"locked": True})

    @require_vault
    def _api_create(self: Handler) -> None:
        body = self._read_json()
        cred = Credential(
            name=(body.get("name") or "").strip(),
            username=body.get("username") or "",
            password=body.get("password") or "",
            url=body.get("url") or "",
            notes=body.get("notes") or "",
            tags=list(body.get("tags") or []),
            custom_fields=list(body.get("custom_fields") or []),
        )
        self.session.vault.add(cred)
        self._json(200, {"entry": _credential_payload(cred)})

    @require_vault
    def _api_update(self: Handler, cred_id: str) -> None:
        existing = self.session.vault.get(cred_id)
        if existing is None:
            self._json(404, {"error": "找不到该条目"})
            return
        body = self._read_json()
        merged = existing.to_dict()
        for key in ("name", "username", "url", "notes", "tags", "custom_fields"):
            if key in body:
                merged[key] = body[key]
        if "password" in body and body["password"] is not None:
            merged["password"] = body["password"]
        if "reveal" in body:
            merged["password"] = existing.password if body["reveal"] else merged.get("password", "")
        updated = Credential.from_dict(merged)
        self.session.vault.update(updated)
        self._json(200, {"entry": _credential_payload(updated)})

    @require_vault
    def _api_delete(self: Handler, cred_id: str) -> None:
        self.session.vault.delete(cred_id)
        self._json(200, {"ok": True})

    def _api_generate(self: Handler) -> None:
        body = self._read_json()
        options = dict(self.session.settings.get("generator") or {})
        options.update({k: v for k, v in body.items() if k in generator.DEFAULT_OPTIONS})
        value = generator.generate_password(options)
        self._json(200, {"password": value, "strength": crypto.password_strength(value)})

    @require_vault
    def _api_change_master(self: Handler) -> None:
        body = self._read_json()
        self.session.vault.change_master_password(body.get("old_password") or "", body.get("new_password") or "")
        self._json(200, {"ok": True, "recovery_key": self.session.vault.last_recovery_key})

    @require_vault
    def _api_rotate_recovery(self: Handler) -> None:
        key = self.session.vault.rotate_recovery_key()
        self._json(200, {"recovery_key": key})

    def _api_settings(self: Handler) -> None:
        body = self._read_json()
        updated = self.session.save_settings(body)
        self._json(200, {"settings": updated})

    @require_vault
    def _api_sync_estimate(self: Handler) -> None:
        vault = self.session.vault
        self._json(200, {"estimate": sync.estimate(len(vault.credentials)),
                         "revision": vault.header.revision, "device": vault.header.device,
                         "count": len(vault.credentials)})

    @require_vault
    def _api_sync_pack(self: Handler) -> None:
        body = self._read_json()
        passphrase = body.get("passphrase") or ""
        if len(passphrase) < 8:
            self._json(400, {"error": "同步口令至少 8 位"})
            return
        self.session.sync_passphrase = passphrase
        vault = self.session.vault
        lines = sync.pack(vault.credentials, passphrase, vault.header.revision, vault.header.device)
        self._json(200, {"lines": lines, "count": len(lines)})

    @require_vault
    def _api_sync_unpack(self: Handler) -> None:
        body = self._read_json()
        passphrase = body.get("passphrase") or ""
        content = body.get("content") or ""
        if not content.strip():
            self._json(400, {"error": "请先粘贴同步字符串"})
            return
        data = sync.unpack(content.splitlines(), passphrase)
        remote_rev = int(data.get("v", 0))
        vault = self.session.vault
        decision = sync.resolve_conflict(vault.header.revision, remote_rev, vault.header.device, str(data.get("device", "")))
        incoming: List[Credential] = data["credentials"]
        payload = {
            "revision": remote_rev,
            "device": data.get("device"),
            "count": len(incoming),
            "decision": decision,
            "local_revision": vault.header.revision,
            "conflict": decision == "conflict",
            "entries": [_credential_payload(c) for c in incoming],
        }
        if body.get("apply"):
            vault.credentials = list(incoming)
            vault.save()
            payload["applied"] = True
        self._json(200, payload)

    @require_vault
    def _api_export(self: Handler) -> None:
        body = self._read_json()
        target = body.get("path")
        if not target:
            self._json(400, {"error": "缺少导出路径"})
            return
        self.session.vault.export_encrypted(target)
        self._json(200, {"ok": True, "path": target})

    Handler.api_state = _api_state                      # type: ignore[attr-defined]
    Handler.api_list = _api_list                        # type: ignore[attr-defined]
    Handler.api_unlock = _api_unlock                    # type: ignore[attr-defined]
    Handler.api_lock = _api_lock                        # type: ignore[attr-defined]
    Handler.api_create = _api_create                    # type: ignore[attr-defined]
    Handler.api_update = _api_update                    # type: ignore[attr-defined]
    Handler.api_delete = _api_delete                    # type: ignore[attr-defined]
    Handler.api_generate = _api_generate                # type: ignore[attr-defined]
    Handler.api_change_master = _api_change_master      # type: ignore[attr-defined]
    Handler.api_rotate_recovery = _api_rotate_recovery  # type: ignore[attr-defined]
    Handler.api_settings = _api_settings                # type: ignore[attr-defined]
    Handler.api_sync_estimate = _api_sync_estimate      # type: ignore[attr-defined]
    Handler.api_sync_pack = _api_sync_pack              # type: ignore[attr-defined]
    Handler.api_sync_unpack = _api_sync_unpack          # type: ignore[attr-defined]
    Handler.api_export = _api_export                    # type: ignore[attr-defined]


_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
]


def _log(message: str) -> None:
    """写运行日志（由入口注入 logger；失败也不影响运行）。"""
    logger = getattr(Session, "logger", None)
    if callable(logger):
        logger(message)


def _open_browser(url: str) -> None:
    """打开浏览器，按可靠性依次尝试并把结果写进日志。

    教训：os.startfile 在 Edge 上走"交给已运行实例"的协议，失败时会**静默返回成功**
    （实测出现过：调用返回成功，但浏览器一个进程都没起来）。
    所以先直接起浏览器进程并带上地址，这条路不依赖系统默认动作与单实例协议。
    """
    import subprocess

    for exe in _BROWSER_CANDIDATES:
        if not os.path.exists(exe):
            continue
        try:
            subprocess.Popen([exe, url], close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            _log(f"已直接启动浏览器：{exe} -> {url}")
            return
        except Exception as exc:
            _log(f"直接启动 {exe} 失败：{type(exc).__name__}: {exc}")

    try:
        subprocess.Popen(["rundll32", "url.dll,FileProtocolHandler", url], close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        _log(f"已用 rundll32 打开默认浏览器：{url}")
        return
    except Exception as exc:
        _log(f"rundll32 打开失败：{type(exc).__name__}: {exc}")

    if os.name == "nt":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            _log(f"已用 os.startfile 打开浏览器：{url}")
            return
        except Exception as exc:
            _log(f"os.startfile 打开浏览器失败：{type(exc).__name__}: {exc}")

    try:
        ok = webbrowser.open(url)
        _log(f"webbrowser.open 返回 {ok}：{url}")
    except Exception as exc:
        _log(f"webbrowser.open 失败：{type(exc).__name__}: {exc}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(data_dir: str, port: Optional[int] = None, open_browser: bool = True,
        logger=None) -> Tuple[ThreadingHTTPServer, str]:
    """启动服务；返回 (server, url)。调用方负责 shutdown。"""
    session = Session(data_dir)
    if callable(logger):
        Session.logger = staticmethod(logger)  # type: ignore[attr-defined]
    _install_api(session)
    Handler.session = session
    httpd = ThreadingHTTPServer(("127.0.0.1", port or _free_port()), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    _log(f"本地服务已监听 127.0.0.1:{httpd.server_address[1]}")
    if open_browser and not os.environ.get("PM_NO_BROWSER"):
        threading.Thread(target=lambda: (time.sleep(0.6), _open_browser(url)), daemon=True).start()
    else:
        _log("已跳过自动打开浏览器")
    return httpd, url
