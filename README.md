# pmvault

本地加密的账号密码管理器。**桌面（Tkinter）/ 浏览器 / Android 三端共用同一套加密内核**。
磁盘上只有密文：AES-256-GCM + Scrypt（可选 Argon2id）+ HKDF 密钥分离，主密码不落盘、也没有后门。

## 目录

```
app/
├── main.py            Flet 应用入口（flet build 的硬要求：应用目录根下必须有 main.py）
├── run_tk.py          桌面端入口（Tkinter，Windows / macOS / Linux 均可）
├── run_web.py         浏览器版入口（本地回环 HTTP 服务 + 原生 JS 前端）
├── pm_core/           ★ 加密内核（纯 Python、无 UI 依赖，三端共用）
│                      crypto / models / vault / generator / sync / sorting
├── pm_ui/             桌面端界面（Tkinter）
├── pm_web/            浏览器版（server.py + static/）
└── pm_android/        Android 界面（Flet）
.github/workflows/     云端构建签名 APK
installer/             Windows 安装包（Inno Setup）脚本
```

## 运行

```bash
pip install cryptography==48.0.0 flet==1.0.4     # 见 pyproject.toml 的 dependencies
# requirements.txt 是作者本机打包环境的锁定版本（多含 pytest / pyinstaller），可选

python app/run_tk.py                   # 桌面端
python app/run_web.py                  # 浏览器版（打开提示的本地地址）
flet run app/main.py                   # Android 界面（桌面预览）
```

## 构建

- **Android APK**：见 `.github/workflows/build-apk.yml`（Actions 里手动触发或推标签），产物在 Artifacts / Releases。
- **Windows 安装包**：`installer/pmvault.iss`（Inno Setup 6 编译），需先用 PyInstaller 把 `app/run_tk.py` 打成 onedir。

## 下载

预编译产物在 [Releases](https://github.com/phis123/pmvault/releases)：

| 文件 | 说明 |
|---|---|
| `PMVault-*-setup.exe` | Windows 安装包（覆盖安装，数据保留） |
| `PMVault-*-desktop.zip` | Windows 免安装版（解压即用） |
| `PMVault-*-arm64.apk` | Android arm64-v8a |

## 安全说明

- 保险库文件（`vault.pmvault`）是密文；**主密码与恢复密钥都在用户手里**，两者都丢失时数据在密码学上无法恢复。
- 仓库**不含**签名私钥：`.p12` / `密码.txt` / `keystore.b64` 已被 `.gitignore` 排除。
  签名信息在 CI 里通过 Secrets（`KEYSTORE_BASE64` / `KEYSTORE_PASSWORD` / `KEY_PASSWORD` / `KEY_ALIAS`）注入。
- 建议自行审计源码后再使用；本项目不联网、无服务端、无遥测。

## 测试

```bash
pip install cryptography==48.0.0 flet==1.0.4 pytest
python tests/run_all.py            # 串行跑完 14 个测试文件并汇总
```

覆盖：加密内核（26 项）、排序规则（29 项）、同步文件导出/导入（25 项）、桌面弹窗定位（20 项）、
桌面字段显示回归、忘记密码/重置流程、Android 界面/布局/导航/重置、浏览器版接口与端到端。

- 桌面相关测试会**真实创建 Tk 窗口**（`withdraw()` 隐藏后测），需要图形环境。
- `tests/test_tk_exe.py` 需要先打包出 `dist_tk/PMVaultTk/`，缺产物时会明确 `[skip]`，不会假红。
- 不是 mock：真建窗口、真起 HTTP 服务、真读写保险库文件。详见 [tests/README.md](tests/README.md)。