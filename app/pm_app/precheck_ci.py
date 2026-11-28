"""CI 预检：确认 Flet 构建时不会弹交互提示。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    problems = []

    try:
        import flet.version as V
        required = V.flutter_version
        fdir = Path.home() / "flutter" / required
        if (fdir / "bin" / "flutter").exists():
            print(f"[ok]   Flutter {required} -> {fdir}")
        else:
            problems.append(f"Flutter {required} 不在 {fdir}")
    except Exception as exc:
        problems.append(f"检查 Flutter 出错: {exc}")

    try:
        from flet_cli.utils.jdk import JDK_MAJOR_VER, check_jdk_version
        java_home = os.environ.get("JAVA_HOME", "")
        if java_home and check_jdk_version(java_home):
            print(f"[ok]   JDK {JDK_MAJOR_VER} (JAVA_HOME={java_home})")
        else:
            problems.append(f"JAVA_HOME 无效或不是 JDK {JDK_MAJOR_VER}: {java_home!r}")
    except Exception as exc:
        problems.append(f"检查 JDK 出错: {exc}")

    try:
        from flet_cli.utils.android_sdk import MINIMAL_PACKAGES, AndroidSDK
        if AndroidSDK.has_minimal_packages_installed():
            print(f"[ok]   Android SDK 最小包齐备: {MINIMAL_PACKAGES}")
        else:
            problems.append(f"Android SDK 不完整（ANDROID_HOME={AndroidSDK.android_home_dir()}）")
    except Exception as exc:
        problems.append(f"检查 Android SDK 出错: {exc}")

    if problems:
        print("\n预检未通过：")
        for item in problems:
            print("  -", item)
        return 1
    print("\n预检通过：flet build 不会再弹交互提示")
    return 0


if __name__ == "__main__":
    sys.exit(main())
