# -*- coding: utf-8 -*-
"""一键跑完 tests/ 下的全部验证。

每个测试文件都是独立脚本（退出码 0 = 通过）。本脚本串行执行并汇总结果。
用法：python tests/run_all.py
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")


def main() -> int:
    files = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    if not files:
        print("没找到测试文件")
        return 1

    env = dict(os.environ)
    env["PYTHONPATH"] = APP + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUTF8"] = "1"

    print("=" * 70)
    print(f"跑 {len(files)} 个测试文件（PYTHONPATH={APP}）")
    print("=" * 70)

    failed, skipped = [], []
    for name in files:
        path = os.path.join(HERE, name)
        proc = subprocess.run([sys.executable, "-X", "utf8", path],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = [ln for ln in out.strip().splitlines() if ln.strip()]
        last = tail[-1].strip() if tail else ""
        if proc.returncode != 0:
            status = "FAIL"
            failed.append(name)
        elif "[skip]" in out:
            status = "SKIP"
            skipped.append(name)
        else:
            status = " ok "
        print(f"  [{status}] {name:<34} {last[:80]}")

    print("=" * 70)
    print(f"通过 {len(files) - len(failed) - len(skipped)} · 跳过 {len(skipped)} · 失败 {len(failed)}")
    if failed:
        print("失败：")
        for f in failed:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())