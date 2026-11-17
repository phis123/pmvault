# tests

自动化验证。**不依赖本机绝对路径**：所有路径都相对本文件解析，被测代码在 `../app/`。

## 准备

```bash
pip install cryptography==48.0.0 flet==1.0.4 pytest
```

## 跑全部

```bash
python tests/run_all.py
```

也可以单个跑（每个文件都是独立脚本，退出码 0 = 通过）：

```bash
python tests/test_core.py          # 这个也支持 pytest：python -m pytest tests/test_core.py -q
python tests/test_sorting.py
python tests/test_sync_file.py
```

## 各文件测什么

| 文件 | 内容 |
|---|---|
| `test_core.py` | 加密内核：加解密往返、KDF、篡改检测、错误口令（pytest 风格，26 项） |
| `test_sorting.py` | 排序规则：忽略大小写、数字按数值、数段先于文段、中文码位、三键升降序、稳定排序、容错（29 项） |
| `test_sync_file.py` | 同步文件导出/导入：往返一致、错误口令、篡改检测、`#` 说明行向后兼容（25 项） |
| `test_desktop_dialogs.py` | 桌面弹窗定位：父窗口分支坐标、未映射父窗口回归、真实同步弹窗（20 项） |
| `test_desktop_ui_fields.py` | 界面值显示回归：防「StringVar 被回收导致空白」，防构造期回调 NameError |
| `test_desktop_reset.py` | 桌面端忘记密码 → 重置/导出流程（归档仍可用原密码解锁、相对路径可移植） |
| `test_desktop_forgot_password.py` | 桌面端「忘记密码」全流程（真 Tk 界面跑完三步） |
| `test_tk_exe.py` | 打包产物（PyInstaller onedir）自检 + 真启动存活检测。**需要先打包**，缺产物时明确 `[skip]` |
| `test_android_ui.py` | Android 界面逻辑（模拟 page，无设备可跑） |
| `test_android_layout.py` | Android 布局：SafeArea 避让、字段高度/字号、解锁按钮尺寸（28 项） |
| `test_android_navigation.py` | Android 导航端到端：列表→编辑→保存→详情→返回 |
| `test_android_reset.py` | Android 忘记密码 → 重置保险库（入口显隐、强确认词、重置后状态） |
| `test_api.py` | 浏览器版本地 HTTP 接口 |
| `test_web_e2e.py` | 浏览器版端到端（本地起服务、走真实 HTTP） |

## 注意

- **需要图形环境**：`test_desktop_*` 会真实创建 Tk 窗口（`withdraw()` 隐藏后测）。无显示环境下会失败。
- `test_tk_exe.py` 需要一个已打包的 `dist_tk/PMVaultTk/`（仓库根下）。它检测的是「exe 能不能起来、窗口是否真的出现并存活」。
- 所有临时文件都落在系统临时目录（`pmvault-tests-*`），不会污染仓库。
- 这几个测试**不是** mock 出来的：它们真的建窗口、真的起 HTTP 服务、真的读写保险库文件。