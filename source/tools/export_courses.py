"""把赛历里全部赛段导出成可编辑的 JSON 初稿。

    python3 source/tools/export_courses.py

导出之后，`data/courses/` 里每个文件都是一条可以手改的赛道。引擎跑比赛时
优先读这些文件，所以改完立刻生效，不需要改任何代码。

**不覆盖已有文件**是刻意的：改过的赛道不该在下次导出时被打回原样。
要重置某一条，删掉对应文件再跑一次即可。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.course_io import COURSE_DIR, course_path, save_course, validate, course_to_dict  # noqa: E402
from game.courses import generate_course  # noqa: E402
from game.world import World  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default="data/world.json")
    ap.add_argument("--force", action="store_true", help="覆盖已经改过的文件")
    args = ap.parse_args()

    world = World.load(ROOT / args.world)
    written = skipped = 0
    by_type: dict[str, int] = {}

    for e in sorted(world.calendar, key=lambda x: x.start_day):
        for i, spec in enumerate(e.stages, start=1):
            cid = f"{e.race_id}-{i}"
            if course_path(cid).exists() and not args.force:
                skipped += 1
                continue
            name = e.name if len(e.stages) == 1 else f"{e.name} 第 {i} 赛段"
            course = generate_course(spec)
            course.name = name
            save_course(course, cid)
            written += 1
            by_type[spec.stage_type] = by_type.get(spec.stage_type, 0) + 1

    print(f"赛道数据已导出 -> {COURSE_DIR.relative_to(ROOT)}/")
    print(f"  新写入 {written} 条，跳过已存在的 {skipped} 条")
    if by_type:
        print("  " + "、".join(f"{k} {v}" for k, v in
                               sorted(by_type.items(), key=lambda x: -x[1])))
    print("\n改完直接生效，不需要动代码。要重置某一条，删掉文件再跑一次。")


if __name__ == "__main__":
    main()
