"""一键运行 PMVault 全量测试（内核、排序、新旧兼容、双通道同步、统一响应式 UI）。"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
TESTS_DIR = os.path.join(ROOT, "tests")


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = APP_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [sys.executable, "-m", "pytest", TESTS_DIR, "-v"]
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
