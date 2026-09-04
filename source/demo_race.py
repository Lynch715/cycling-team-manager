"""跑一场比赛并打印结果。

    python source/demo_race.py mountain
    python source/demo_race.py flat --teams 20 --seed 42
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sim import (  # noqa: E402
    Race, build_peloton, cobbled_stage, flat_stage, format_gap, format_time,
    itt_stage, mountain_stage,
)

STAGES = {
    "flat": flat_stage,
    "mountain": mountain_stage,
    "itt": itt_stage,
    "cobbled": cobbled_stage,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", nargs="?", default="mountain", choices=sorted(STAGES))
    ap.add_argument("--teams", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dt", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    course = STAGES[args.stage]()
    riders = build_peloton(n_teams=args.teams, seed=args.seed)

    print(f"\n{course.name} · {course.length_m / 1000:.1f} km · "
          f"累计爬升 {course.total_ascent_m:.0f} m · {len(riders)} 名车手")
    print("=" * 74)

    result = Race(course, riders, dt=args.dt, seed=args.seed).run()
    winner = result.winner
    if winner is None:
        print("无人完赛")
        return

    avg_kmh = course.length_m / winner.finish_time * 3.6
    print(f"冠军成绩 {format_time(winner.finish_time)} · 均速 {avg_kmh:.1f} km/h\n")

    print(f"{'名次':<5}{'车手':<14}{'车队':<7}{'成绩/差距':<11}"
          f"{'均功率':>7}{'峰值':>7}{'W/kg':>7}{'剩余W′':>8}")
    print("-" * 74)
    for place, s, gap in result.standings()[:args.top]:
        r = s.rider
        t = format_time(s.finish_time) if place == 1 else format_gap(gap)
        print(f"{place:<5}{r.name:<14}{r.team_id:<7}{t:<11}"
              f"{s.avg_power:>7.0f}{s.peak_power:>7.0f}"
              f"{s.avg_power / r.body_mass_kg:>7.2f}{s.energy.w_fraction:>7.0%}")

    print(f"\n完赛 {len(result.finishers)} 人，关门 {len(result.dnf)} 人")

    roles = Counter(s.rider.role.value for s in result.finishers[:10])
    print("前十角色分布：" + "、".join(f"{k} {v}" for k, v in roles.most_common()))

    attacks = [(t, x) for t, x in result.events if "攻击" in x]
    drops = [(t, x) for t, x in result.events if "掉队" in x]

    print("\n比赛过程：")
    for t, text in attacks[:4] + attacks[-4:][-4:] if attacks else []:
        print(f"  [{format_time(t)}] {text}")
    if drops:
        print(f"  集团在途中被撕开 {len(drops)} 次，首次发生在 "
              f"[{format_time(drops[0][0])}]，{drops[0][1]}")
    print(f"  [{format_time(winner.finish_time)}] {result.events[-1][1]}")

    packs = result.group_gaps(3.0)[:5]
    print("完赛分组：" + "、".join(
        f"{n} 人{'（同时间）' if g < 0.5 else format_gap(g)}" for n, g in packs))


if __name__ == "__main__":
    main()
