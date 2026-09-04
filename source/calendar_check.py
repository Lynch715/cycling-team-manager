"""赛历体检与导出。

    python3 source/calendar_check.py            # 体检当前赛历
    python3 source/calendar_check.py --export   # 把内置默认赛历导出成可编辑 JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game.calendar_io import (  # noqa: E402
    CALENDAR_PATH, analyse, render, save, validate,
)


def default_rows() -> list[dict]:
    from game.generate_world import CALENDAR_SEEDS
    return [{"id": f"E{i + 1:02d}", "name": n, "tier": t.value, "country": c,
             "start_day": d, "prestige": p, "stages": s, "terrain": terr}
            for i, (n, t, c, d, p, s, terr) in enumerate(CALENDAR_SEEDS)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.export:
        if CALENDAR_PATH.exists() and not args.force:
            print(f"{CALENDAR_PATH.name} 已存在，不覆盖。要重置加 --force。")
        else:
            p = save(default_rows())
            print(f"赛历已导出 -> {p.relative_to(Path(__file__).resolve().parents[1])}")
            print("改完再跑一次本脚本体检，然后跑 generate_world.py 生效。")
        return

    if CALENDAR_PATH.exists():
        d = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
        source = "data/calendar.json"
    else:
        d = {"races": default_rows()}
        source = "内置默认赛历（尚未导出）"

    print(f"数据源：{source}\n")
    issues = validate(d)
    if issues:
        for i in issues:
            print(f"  [{i.level}] {i.where}：{i.text}")
        print()
    else:
        print("  校验通过，没有错误。\n")

    print(render(analyse(d)))


if __name__ == "__main__":
    main()
