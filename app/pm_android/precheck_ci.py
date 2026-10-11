"""CI 预检：确认 Flet 构建时不会弹交互提示。

放在 app/precheck_ci.py，由 workflow 直接执行。
Flet 在缺依赖时会用 Confirm.ask 询问 y/n，CI 里没有标准输入会 EOFError 失败，
所以构建前必须先确认这三样都已就位：
  1) Flutter   → Flet 写死找 ~/flutter/<flutter_version>
  2) JDK 17    → Flet 认 JAVA_HOME，且要求主版本为 17
  3) Android SDK → cmdline-tools + platform-tools + platforms;android-35 + build-tools;34.0.0
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    problems = []

    # 1) Flutter
    try:
        import flet.version as V
        required = V.flutter_version
        fdir = Path.home() / "flutter" / required
        executable = fdir / "bin" / "flutter"
        if executable.exists():
            print(f"[ok]   Flutter {required} -> {fdir}")
        else:
            problems.append(f"Flutter {required} 不在 {fdir}")
            print(f"[FAIL] Flutter {required} 不在 {fdir}")
    except Exception as exc:
        problems.append(f"检查 Flutter 出错: {exc}")
        print(f"[FAIL] 检查 Flutter 出错: {exc}")

    # 2) JDK
    try:
        from flet_cli.utils.jdk import JDK_MAJOR_VER, check_jdk_version
        java_home = os.environ.get("JAVA_HOME", "")
        if java_home and check_jdk_version(java_home):
            print(f"[ok]   JDK {JDK_MAJOR_VER} (JAVA_HOME={java_home})")
        else:
            problems.append(f"JAVA_HOME 无效或不是 JDK {JDK_MAJOR_VER}: {java_home!r}")
            print(f"[FAIL] JAVA_HOME 无效或版本不符: {java_home!r}")
    except Exception as exc:
        problems.append(f"检查 JDK 出错: {exc}")
        print(f"[FAIL] 检查 JDK 出错: {exc}")

    # 3) Android SDK（直接用 Flet 自己的判定函数）
    try:
        from flet_cli.utils.android_sdk import MINIMAL_PACKAGES, AndroidSDK
        if AndroidSDK.has_minimal_packages_installed():
            print(f"[ok]   Android SDK 最小包齐备: {MINIMAL_PACKAGES}")
        else:
            home = AndroidSDK.android_home_dir()
            problems.append(f"Android SDK 不完整（ANDROID_HOME={home}），需要 {MINIMAL_PACKAGES}")
            print(f"[FAIL] Android SDK 不完整（ANDROID_HOME={home}）")
    except Exception as exc:
        problems.append(f"检查 Android SDK 出错: {exc}")
        print(f"[FAIL] 检查 Android SDK 出错: {exc}")

    print(f"\nANDROID_HOME={os.environ.get('ANDROID_HOME')}")
    print(f"JAVA_HOME={os.environ.get('JAVA_HOME')}")

    if problems:
        print("\n预检未通过：")
        for item in problems:
            print("  -", item)
        return 1
    print("\n预检通过：flet build 不会再弹交互提示")
    return 0


if __name__ == "__main__":
    sys.exit(main())
