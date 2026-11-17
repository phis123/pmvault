# -*- coding: utf-8 -*-
"""排序功能验证（pm_core.sorting + Vault.search 集成 + 两端默认设置）。

覆盖 PR 声明的规则：忽略大小写 / 数字按数值 / 数字段先于文字段 / 中文按码位 /
三键升降序 / 稳定排序 / 缺失时间 / 非法键回退 / 不修改入参。
"""

from __future__ import annotations

# ── 可移植引导（仓库版）：所有路径相对本文件解析 ──────────────────────
# 测试假定：本文件在 <repo>/tests/，被测代码在 <repo>/app/。
# 自带导入，避免插在各文件自己的 import 之前时 os 还没有名字。
import os as _os_boot
import tempfile as _tempfile_boot
_HERE = _os_boot.path.dirname(_os_boot.path.abspath(__file__))
_APP_DIR = _os_boot.path.join(_os_boot.path.dirname(_HERE), "app")
_WORK_ROOT = _tempfile_boot.mkdtemp(prefix="pmvault-tests-")


import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

BASE = _APP_DIR
for p in (BASE,):
    if p not in sys.path:
        sys.path.insert(0, p)

import pm_core  # noqa: E402
from pm_core import Credential, Vault, sorting  # noqa: E402

FAIL = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  [ok]   " if ok else "  [FAIL] ") + label + ("" if ok else f"   {detail}"))
    if not ok:
        FAIL.append(label)


def names(creds) -> list:
    return [c.name for c in creds]


def make(name: str, password: str = "x", created_delta: int = 0, updated_delta: int = 0) -> Credential:
    now = datetime.now(timezone.utc).astimezone()
    c = Credential(name=name, password=password)
    c.created_at = (now + timedelta(days=created_delta)).isoformat()
    c.updated_at = (now + timedelta(days=updated_delta)).isoformat()
    return c


def main() -> int:
    print("=" * 66)
    print("排序功能验证")
    print("=" * 66)

    # ---------------------------------------------------------------- 名称规则
    print("\n【1】名称排序规则")
    mixed = [make(n) for n in ["Banana", "apple", "cherry"]]
    check("忽略大小写（apple 在 Banana 前）",
          names(sorting.sort_credentials(mixed, sorting.SORT_NAME)) == ["apple", "Banana", "cherry"])

    numeric = [make(n) for n in ["条目10", "条目2", "条目1"]]
    check("连续数字按数值比较（条目2 < 条目10）",
          names(sorting.sort_credentials(numeric, sorting.SORT_NAME)) == ["条目1", "条目2", "条目10"])

    mixedseg = [make(n) for n in ["a1", "1a", "b"]]
    check("数字段整体排在文字段之前（1a < a1 < b）",
          names(sorting.sort_credentials(mixedseg, sorting.SORT_NAME)) == ["1a", "a1", "b"])

    cn = [make(n) for n in ["张三", "李四", "王五"]]
    check("中文按 Unicode 码位（张<李<王）",
          names(sorting.sort_credentials(cn, sorting.SORT_NAME)) == ["张三", "李四", "王五"])

    check("name_key 对空值不崩", sorting.name_key(None) == () or isinstance(sorting.name_key(None), tuple))

    # ---------------------------------------------------------------- 三键升降序
    print("\n【2】三个键 × 升降序")
    raw = [make("B", created_delta=-2, updated_delta=-1),
           make("A", created_delta=-5, updated_delta=-3),
           make("C", created_delta=-1, updated_delta=-9)]
    check("created 升序（旧→新）",
          names(sorting.sort_credentials(raw, sorting.SORT_CREATED, False)) == ["A", "B", "C"])
    check("created 降序（新→旧）",
          names(sorting.sort_credentials(raw, sorting.SORT_CREATED, True)) == ["C", "B", "A"])
    check("updated 升序",
          names(sorting.sort_credentials(raw, sorting.SORT_UPDATED, False)) == ["C", "A", "B"])
    check("updated 降序",
          names(sorting.sort_credentials(raw, sorting.SORT_UPDATED, True)) == ["B", "A", "C"])
    check("name 升序", names(sorting.sort_credentials(raw, sorting.SORT_NAME, False)) == ["A", "B", "C"])
    check("name 降序", names(sorting.sort_credentials(raw, sorting.SORT_NAME, True)) == ["C", "B", "A"])

    # ---------------------------------------------------------------- 稳定性与容错
    print("\n【3】稳定性与容错")
    same = [make("same", created_delta=0) for _ in range(3)]
    for i, c in enumerate(same):
        c.tags = [f"tag{i}"]
    ordered = sorting.sort_credentials(same, sorting.SORT_CREATED)
    check("相等条目保持原有相对顺序（稳定排序）",
          [c.tags[0] for c in ordered] == ["tag0", "tag1", "tag2"])

    broken = [make("no-time"), make("with-time", created_delta=-1)]
    broken[0].created_at = "not-a-date"
    check("时间解析失败按最小值（不会崩、排在最前）",
          names(sorting.sort_credentials(broken, sorting.SORT_CREATED)) == ["no-time", "with-time"])
    broken[0].created_at = ""
    check("时间为空同样按最小值处理",
          names(sorting.sort_credentials(broken, sorting.SORT_CREATED)) == ["no-time", "with-time"])

    check("非法排序键回退到默认键", sorting.normalize_sort_key("garbage") == sorting.DEFAULT_SORT_KEY)
    check("None 排序键回退到默认键", sorting.normalize_sort_key(None) == sorting.DEFAULT_SORT_KEY)
    check("大小写/空格容错", sorting.normalize_sort_key("  NAME ") == sorting.SORT_NAME)

    src = [make("c"), make("a"), make("b")]
    before = names(src)
    sorting.sort_credentials(src, sorting.SORT_NAME)
    check("不修改传入列表顺序", names(src) == before)

    check("sort_label 名称↑/名称↓",
          sorting.sort_label("name", False) == "名称↑" and sorting.sort_label("name", True) == "名称↓")
    check("sort_caption 带方向说明",
          sorting.sort_caption("created", False) == "创建时间（递增）")

    # ---------------------------------------------------------------- Vault.search
    print("\n【4】Vault.search 集成")
    tmpdir = tempfile.mkdtemp(prefix="pm-sort-")
    vault = Vault.create(os.path.join(tmpdir, "v.pmvault"), "正确密码123")
    for n in ["条目10", "条目2", "alpha"]:
        vault.add(Credential(name=n, password="Pw!" + n))
    check("不传排序参数时保持插入顺序（向后兼容）",
          names(vault.search("")) == ["条目10", "条目2", "alpha"])
    check("传 name 排序生效", names(vault.search("", sort_key="name")) == ["alpha", "条目2", "条目10"])
    check("搜索 + 排序组合", names(vault.search("条目", sort_key="name", sort_desc=True)) == ["条目10", "条目2"])
    check("旧式单参数调用不报错", isinstance(vault.search("alpha"), list))

    # ---------------------------------------------------------------- 默认设置
    print("\n【5】两端默认设置与持久化")
    from pm_android import state as android_state  # noqa: E402

    d = android_state.DEFAULT_SETTINGS
    check("安卓 DEFAULT_SETTINGS 含 sort_key", d.get("sort_key") == sorting.DEFAULT_SORT_KEY)
    check("安卓 DEFAULT_SETTINGS 含 sort_desc", d.get("sort_desc") is sorting.DEFAULT_SORT_DESC)

    from pm_ui import settings as ui_settings  # noqa: E402

    ud = ui_settings.DEFAULT_SETTINGS
    check("桌面 DEFAULT_SETTINGS 含 sort_key", ud.get("sort_key") == "created")
    check("桌面 DEFAULT_SETTINGS 含 sort_desc", ud.get("sort_desc") is False)

    check("pm_core 导出 sorting", hasattr(pm_core, "sorting") and "sorting" in pm_core.__all__)

    print("\n" + "=" * 66)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("排序功能验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
