"""赛道体检：给一条或全部赛道出一份「这会跑成什么样」的报告。

    python3 source/course_check.py E13-5      # 单条
    python3 source/course_check.py --all      # 全部，只列出有问题的
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game.course_io import list_courses, load_course, validate, course_path  # noqa: E402
from game.course_report import analyse, render  # noqa: E402

import json  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("course_id", nargs="?")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        ids = list_courses()
        print(f"共 {len(ids)} 条赛道\n")
        bad = 0
        for cid in ids:
            d = json.loads(course_path(cid).read_text(encoding="utf-8"))
            issues = validate(d)
            if issues:
                bad += 1
                print(f"{cid}　{d.get('name', '')}")
                for i in issues[:4]:
                    print(f"   [{i.level}] {i.where}：{i.text}")
        print(f"\n{len(ids) - bad} 条无问题，{bad} 条有提示")
        return

    if not args.course_id:
        raise SystemExit("请给一个赛道 id，或用 --all")
    c = load_course(args.course_id)
    print(render(analyse(c)))


if __name__ == "__main__":
    main()
