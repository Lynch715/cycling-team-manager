"""对账：快速结算 vs 完整引擎。

两套结算必须描述同一个世界。这个脚本把它们放在同一条赛道、同一批车手上
跑，比对四件事：冠军成绩、差距分布（中位数/80 分位）、亚军差距、
以及最关键的——前十名的角色构成。

角色构成是最硬的指标。均速差 2% 玩家察觉不到，但如果完整引擎里
平路赛段是冲刺手赢、快速结算里变成爬坡手赢，那就是两个游戏。

    python3 source/calibrate_quick.py
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import math  # noqa: E402
from game.quickresolve import RACE_SIGMA, resolve  # noqa: E402
from sim import (  # noqa: E402
    Race, build_peloton, cobbled_stage, flat_stage, format_time, itt_stage,
    mountain_stage,
)

OK, BAD = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def pct(sorted_gaps: list[float], q: float) -> float:
    if not sorted_gaps:
        return 0.0
    return sorted_gaps[min(len(sorted_gaps) - 1, int(len(sorted_gaps) * q))]


def compare(label: str, course, stage_type: str, teams: int = 20,
            seed: int = 42) -> bool:
    riders = build_peloton(teams, 7)

    full = Race(course, riders, dt=1.0, seed=seed).run()
    full_gaps = sorted(g for _, _, g in full.standings())
    full_roles = Counter(s.rider.role.value for _, s, _ in full.standings()[:10])

    # 对账时必须把两层噪声都加上，否则量到的方差偏小
    frng = random.Random(seed + 7)
    form = {r.rider_id: math.exp(frng.gauss(0.0, RACE_SIGMA[stage_type]))
            for r in riders}
    quick = resolve(riders, course, stage_type, random.Random(seed), form=form)
    q_win = quick.times[quick.order[0]]
    quick_gaps = sorted(quick.times[r] - q_win for r in quick.order)
    by_id = {r.rider_id: r for r in riders}
    quick_roles = Counter(by_id[r].role.value for r in quick.order[:10])

    rows = [
        ("冠军成绩", full.winner.finish_time, q_win, 0.03),
        ("亚军差距", full_gaps[1], quick_gaps[1], None),
        ("中位差距", pct(full_gaps, 0.5), pct(quick_gaps, 0.5), None),
        ("80 分位差距", pct(full_gaps, 0.8), pct(quick_gaps, 0.8), None),
    ]

    print(f"\n[{label}]  完整引擎 vs 快速结算")
    good = True
    for name, a, b, tol in rows:
        if tol is not None:
            ratio = abs(b - a) / max(a, 1e-6)
            mark = OK if ratio <= tol else BAD
            good &= ratio <= tol
            print(f"  {mark} {name:<12}{format_time(a):>12}"
                  f"{format_time(b):>12}    偏差 {ratio:.1%}")
        else:
            print(f"    {name:<12}{a:>10.0f} 秒{b:>10.0f} 秒")

    # 角色构成用重叠度衡量：两边前十里同角色的最小计数之和 / 10
    overlap = sum((full_roles & quick_roles).values()) / 10.0
    mark = OK if overlap >= 0.6 else BAD
    good &= overlap >= 0.6
    print(f"  {mark} 前十角色重叠      {overlap:.0%}")
    print(f"    完整引擎：{dict(full_roles)}")
    print(f"    快速结算：{dict(quick_roles)}")
    return good


def main() -> None:
    print("=" * 72)
    print("快速结算 · 完整引擎对账")
    print("=" * 72)
    results = [
        compare("平路赛段", flat_stage(), "flat"),
        compare("山地赛段", mountain_stage(), "summit_finish"),
        compare("个人计时赛", itt_stage(), "itt"),
        compare("石板路赛段", cobbled_stage(), "cobbled"),
    ]
    print("\n" + "=" * 72)
    print(f"通过 {sum(results)}/{len(results)} 项")


if __name__ == "__main__":
    main()
