"""跑一段完整生涯并导出成可浏览的 HTML。

    python3 source/tools/export_career.py --team T08 --seasons 5

这个原型和前两个不一样：`原型_比赛回放` 验证的是美术，`原型_车队经理`
验证的是战术选择，这一个验证的是**时间**——五个赛季下来，一支队会不会
长出自己的故事线。

它是回放而不是实况。真正要玩，得有一个能跑 Python 的后端；但要判断
「这些系统凑在一起会不会有意思」，回放足够了，而且诚实：里面每一条动态、
每一次转会、每一个成长数字，都是引擎真跑出来的。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.career import DecisionKind, new_career  # noqa: E402
from game.management import relegation_table, rolling_points  # noqa: E402
from game.training import CAMPS  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "career_template.html"


def snapshot_roster(c) -> list[dict]:
    return [{
        "id": r.rider_id, "name": r.name, "nation": r.nation, "age": r.age,
        "role": r.role.value, "overall": r.overall, "potential": r.potential,
        "salary": r.salary, "contract": r.contract_years,
        "portrait": r.art_portrait, "morale": round(r.morale, 2),
        "training": r.training.get("program", "allround") if r.training else "",
    } for r in sorted(c.roster, key=lambda r: -r.overall)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="T08")
    ap.add_argument("--seasons", type=int, default=5)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--camp", default="altitude", help="休赛期集训营")
    args = ap.parse_args()

    c = new_career(args.team, args.seed)
    camp_index = [x.key for x in CAMPS].index(args.camp)
    seasons = []

    for _ in range(args.seasons):
        year = c.world.season
        roster_before = snapshot_roster(c)
        guard = 0
        while c.world.season == year and guard < 400:
            guard += 1
            c.advance()
            while c.pending:
                d = c.pending[0]
                if d.kind is DecisionKind.TRAINING:
                    # 付不起就不集训。一个总是选最贵选项的"玩家"
                    # 会把队伍拖垮，那不是系统的问题
                    afford = c.team.budget > 700
                    c.resolve(0, camp_index if afford else 0)
                elif d.kind is DecisionKind.TRANSFER:
                    c.resolve(0, 0)          # 总是签名单上的第一个
                else:
                    c.resolve(0, d.default)

        season_log = list(c.season_log)
        h = c.history[-1]
        seasons.append({
            "year": year,
            "rank": h["rank"], "points": h["points"], "wins": h["wins"],
            "division": h["division"], "budget": h["budget"],
            "payroll": sum(r["salary"] for r in roster_before),
            "roster": roster_before,
            "roster_after": snapshot_roster(c),
            "results": list(c.season_results),
            "headlines": [asdict(x) for x in season_log],
        })

    board = [{"team": t.name.split("-")[0], "division": t.division.value,
              "points": pts, "tag": tag, "mine": t.team_id == args.team}
             for t, pts, tag in relegation_table(c.world)]

    payload = {
        "team": {
            "name": c.team.name, "short": c.team.short_name,
            "division": c.team.division.value,
            "primary": c.team.color_primary,
            "secondary": c.team.color_secondary,
            "badge": c.team.art_badge,
        },
        "camp": args.camp,
        "seasons": seasons,
        "board": board,
    }

    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "career.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__CAREER__*/null", json.dumps(payload, ensure_ascii=False))
    out = ROOT / "原型_生涯回放.html"
    out.write_text(html, encoding="utf-8")

    print(f"生涯回放已生成 -> {out.name}（{out.stat().st_size / 1024:.0f} KB）")
    print(f"  {c.team.name}　{args.seasons} 个赛季")
    for s in seasons:
        print(f"　{s['year']}  {s['division']}  排名第 {s['rank']:<2}  "
              f"积分 {s['points']:<6} 冠军 {s['wins']}  "
              f"动态 {len(s['headlines'])} 条")


if __name__ == "__main__":
    main()
