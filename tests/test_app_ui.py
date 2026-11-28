"""pm_app 统一响应式 UI 测试（无需真机或图形桌面，使用轻量模拟 Page 验证三断点与全业务流）。"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import flet as ft

from pm_core import Credential, Vault, crypto
from pm_app.state import AppState
from pm_app.theme import layout_mode
from pm_app.screens.login import LoginScreen
from pm_app.screens.onboarding import MandatoryResetScreen, RecoveryDisplayScreen, V1UpgradeScreen
from pm_app.screens.vault_view import VaultScreen
from pm_app.screens.detail_view import DetailView
from pm_app.screens.editor_view import EditorView


class DummyPage:
    def __init__(self, width: float = 1140, height: float = 720) -> None:
        self.width = width
        self.height = height
        self.controls = []
        self.overlay = []
        self.drawer = None
        self.theme_mode = ft.ThemeMode.LIGHT
        self.platform_brightness = ft.Brightness.LIGHT
        self.bgcolor = "#F5F7FA"
        self.title = ""
        self.padding = 0
        self.spacing = 0
        self.dialogs = []
        self.clipboard_val = ""

    def add(self, *ctrls):
        self.controls.extend(ctrls)

    def update(self):
        pass

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop()

    def set_clipboard(self, val: str):
        self.clipboard_val = val

    def launch_url(self, url: str):
        pass


def test_responsive_breakpoints():
    assert layout_mode(1280) == "wide"
    assert layout_mode(960) == "wide"
    assert layout_mode(768) == "medium"
    assert layout_mode(600) == "medium"
    assert layout_mode(390) == "narrow"


def test_vault_screen_three_modes_and_detail_editor():
    with tempfile.TemporaryDirectory() as td:
        data_dir = os.path.join(td, "data")
        key_dir = os.path.join(td, "keys")
        state = AppState(data_dir=data_dir, keyfile_dir=key_dir)
        page = DummyPage(width=1140)
        state.page = page

        rec, vault, _ = state.registry.create_vault("个人库", "GoodPass1!", use_keyfile=True)
        state.select_vault_id(rec.id)
        state.vault = vault
        state.active_keyfile_bytes = state.load_keyfile_for_current()

        cred = vault.add(
            Credential(
                name="GitHub",
                url="https://github.com",
                username="phis123",
                password="Strong!Password#9",
                totp_secret="JBSWY3DPEHPK3PXP",
                recovery_phone="13800001111",
                recovery_email="sec@example.com",
                recovery_items=[
                    {"type": "qa", "question": "出生城市", "answer": "青岛"},
                    {"type": "backup_codes", "label": "Google 10组", "codes": [{"code": "1111 2222", "used": False}]},
                ],
                api_keys=[{"label": "PAT", "value": "ghp_xxx", "note": "repo"}],
                tags=["开发"],
                favorite=True,
            )
        )

        # 宽屏三栏渲染
        vs = VaultScreen(state, on_lock=lambda: None, on_show_new_recovery=lambda _t, _r: None, on_theme_changed=lambda: None)
        ctrl = vs.build()
        assert isinstance(ctrl, ft.Control)
        vs.tick_totp()

        # 切到中屏（平板竖屏）
        page.width = 720
        vs._render_layout()

        # 切到窄屏（手机竖屏）并测试列表 -> 详情 -> 编辑 -> 返回
        page.width = 390
        vs._render_layout()
        vs.open_editor(cred)
        assert vs._sub_view == "editor"
        assert vs.handle_back() is True
        assert vs._sub_view == "list"

        # 验证详情与编辑器控件可正常构建
        dv = DetailView(state, on_edit=lambda _c: None, on_changed=lambda: None)
        dv.set_credential(cred)
        ev = EditorView(state, on_saved=lambda _c: None, on_cancel=lambda: None)
        ev.load(cred)
        assert ev.tf_name.value == "GitHub"
        assert ev.tf_totp.value == "JBSWY3DPEHPK3PXP"


def test_login_and_onboarding_screens_build():
    with tempfile.TemporaryDirectory() as td:
        state = AppState(data_dir=os.path.join(td, "data"), keyfile_dir=os.path.join(td, "keys"))
        state.page = DummyPage(width=390)

        # 空状态登录页
        ls = LoginScreen(
            state,
            on_unlocked=lambda: None,
            on_need_v1_upgrade=lambda: None,
            on_need_recovery_reset=lambda: None,
            on_vault_created=lambda _r, _k: None,
        )
        assert isinstance(ls.build(), ft.Control)

        # 创建库后登录页
        rec, vault, _ = state.registry.create_vault("测试库", "TestPass123!", use_keyfile=False)
        state.select_vault_id(rec.id)
        state.vault = vault
        assert isinstance(ls.build(), ft.Control)

        # 三个安全引导页均可正常构建
        assert isinstance(
            RecoveryDisplayScreen(state, title="T", subtitle="S", recovery_key="AAAA-BBBB-CCCC-DDDD-EEEE-FFFF", on_continue=lambda: None).build(),
            ft.Control,
        )
        assert isinstance(
            MandatoryResetScreen(state, on_reset_done=lambda _r, _k: None, on_cancel=lambda: None).build(),
            ft.Control,
        )
        assert isinstance(
            V1UpgradeScreen(state, on_upgraded=lambda _r, _k: None, on_cancel=lambda: None).build(),
            ft.Control,
        )
