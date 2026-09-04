"""生成赛历编辑器。

    python3 source/tools/build_calendar_editor.py

判断逻辑是 calendar_io 的 JS 镜像，和赛道编辑器一个规矩：
编辑器里看到的体检结果必须和 Python 侧一致，否则工具就是在误导人。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.calendar_io import CALENDAR_PATH, analyse, validate  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "calendar_template.html"


def main() -> None:
    if not CALENDAR_PATH.exists():
        raise SystemExit("先跑 python3 source/calendar_check.py --export")
    d = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    races = d["races"]

    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__CAL__*/null", json.dumps(races, ensure_ascii=False))
    out = ROOT / "赛历编辑器.html"
    out.write_text(html, encoding="utf-8")

    rep = analyse(d)
    print(f"赛历编辑器已生成 -> {out.name}（{out.stat().st_size / 1024:.0f} KB）")
    print(f"  {rep.total_races} 场赛事、{rep.total_days} 个比赛日")
    issues = validate(d)
    print(f"  校验：{len(issues)} 条提示　体检：{len(rep.notes)} 条建议")
    print("\n双击打开，改完导出 calendar.json 覆盖 data/ 下的同名文件，"
          "再跑 generate_world.py 生效。")


if __name__ == "__main__":
    main()
