"""把赛道数据打进编辑器模板，产出可双击打开的编辑器。

    python3 source/tools/build_editor.py                # 全部赛道
    python3 source/tools/build_editor.py --filter E13   # 只装某场赛事

编辑器里的判断逻辑是 course_report 的 JS 镜像，公式刻意保持一致——
作者在编辑器里看到的预测，必须和引擎真跑一场的结果对得上，
否则这个工具就是在误导人。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.course_io import COURSE_DIR, list_courses  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "editor_template.html"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="", help="只装 id 里含这个字符串的赛道")
    ap.add_argument("--limit", type=int, default=120)
    args = ap.parse_args()

    ids = [c for c in list_courses() if args.filter in c][:args.limit]
    if not ids:
        raise SystemExit("没有找到赛道，先跑 export_courses.py")

    data = {cid: json.loads((COURSE_DIR / f"{cid}.json").read_text(encoding="utf-8"))
            for cid in ids}

    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__COURSES__*/null", json.dumps(data, ensure_ascii=False))
    out = ROOT / "赛道编辑器.html"
    out.write_text(html, encoding="utf-8")

    print(f"赛道编辑器已生成 -> {out.name}（{out.stat().st_size / 1024:.0f} KB）")
    print(f"  装入 {len(ids)} 条赛道")
    types: dict[str, int] = {}
    for d in data.values():
        types[d.get("stage_type", "?")] = types.get(d.get("stage_type", "?"), 0) + 1
    print("  " + "、".join(f"{k} {v}" for k, v in
                           sorted(types.items(), key=lambda x: -x[1])))
    print("\n双击打开，改完点「导出 JSON」，把文件放回 data/courses/ 覆盖同名文件。")


if __name__ == "__main__":
    main()
