"""连跑多个赛季，检验经营层的长期行为是否合理。

    python3 source/run_career.py --seasons 6

看三件事：
  1. 有没有一支队滚雪球式地永远赢（说明反馈回路没有阻尼）
  2. 车手会不会成长、变老、退役，新人能不能顶上来
  3. 财务会不会崩（全员破产或者全员富得流油都是失败）
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game.generate_world import generate  # noqa: E402
from game.management import advance_season, settle_finance  # noqa: E402
from game.season import run_season  # noqa: E402
from game.world import Division, World  # noqa: E402


def snapshot(world: World, season_out, label: str) -> None:
    top = season_out.team_ranking()[:3]
    names = "、".join(world.team(t).name.split("-")[0] for t, _ in top)
    best = season_out.rider_ranking()[0]
    r = world.rider(best[0])
    # 世界水平指标：前 20 名车手的平均总评。这个数字如果逐年下滑，
    # 说明青训补进来的人顶不上退役的人，世界在慢慢变弱。
    elite = sorted((x.overall for x in world.riders), reverse=True)[:20]
    print(f"{label}  车队前三：{names:<16}"
          f"车手第一：{r.name}（{r.age}岁 {r.role.value} {r.overall}分）"
          f"  世界前20均分 {sum(elite) / len(elite):.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, default=6)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    world = generate(args.seed)
    champions: list[str] = []

    print(f"{'=' * 74}\n多赛季推演：{args.seasons} 个赛季\n{'=' * 74}\n")

    for i in range(args.seasons):
        year = world.season
        outcome = run_season(world, seed=args.seed + i, engine="quick")
        snapshot(world, outcome, f"{year}  ")
        champions.append(outcome.team_ranking()[0][0])

        report = advance_season(world, outcome, rng)
        for line in report["news"][:2]:
            print(f"        {line}")
        for c in report["division_changes"]:
            arrow = "↑" if c.new.value in ("世巡赛", "职业队") and \
                c.old.value in ("职业队", "洲际队") and \
                ["洲际队", "职业队", "世巡赛"].index(c.new.value) > \
                ["洲际队", "职业队", "世巡赛"].index(c.old.value) else "↓"
            extra = f"，{len(c.released)} 名球星触发解约条款离队" if c.released else ""
            print(f"        {arrow} {c.team_name.split('-')[0]} "
                  f"{c.old.value} → {c.new.value}{extra}")
        t = report["transfers"]
        if t.retirements or t.signings:
            print(f"        退役 {len(t.retirements)} 人，"
                  f"转会 {len(t.signings)} 人，"
                  f"无人问津 {len(t.unsigned)} 人")
        print()

    print("=" * 74)
    print("检查一：冠军是否轮换（同一支队连霸说明反馈回路失控）")
    from collections import Counter
    tally = Counter(champions)
    for tid, n in tally.most_common():
        print(f"  {world.team(tid).short_name:<6}{n} 次赛季第一")
    print(f"  → {len(tally)} 支不同的队在 {args.seasons} 个赛季里拿过第一")

    print("\n检查二：年龄结构（应当是金字塔，不能全是老将或全是新人）")
    buckets: dict[str, int] = {}
    for r in world.riders:
        key = ("20-23" if r.age <= 23 else "24-27" if r.age <= 27
               else "28-31" if r.age <= 31 else "32+")
        buckets[key] = buckets.get(key, 0) + 1
    for k in ("20-23", "24-27", "28-31", "32+"):
        n = buckets.get(k, 0)
        print(f"  {k:<7}{'█' * (n // 3):<28}{n} 人")

    print("\n检查三：财务健康度（薪资 / 预算）")
    fin = settle_finance(world, outcome)
    for div in (Division.WORLD, Division.PRO, Division.CONTI):
        rows = [fin[t.team_id] for t in world.teams if t.division is div]
        ratios = [f.salaries / max(1, f.sponsor_income) for f in rows]
        broke = sum(1 for f in rows if f.balance < 0)
        print(f"  {div.value:<5}薪资占预算 {min(ratios):.0%}–{max(ratios):.0%}，"
              f"{broke}/{len(rows)} 支亏损")

    from game.management import relegation_table, rolling_points
    print("\n升降级形势（三年滚动积分，这是玩家每年最该盯的一张表）：")
    for tm, pts, tag in relegation_table(world):
        if tag:
            print(f"  {tm.division.value:<5}{tm.name.split('-')[0]:<12}"
                  f"{pts:>7} 分　{tag}")

    print("\n最后一个赛季的世界排名前八（已退役的不再列出）：")
    shown = 0
    for rid, pts in outcome.rider_ranking():
        try:
            r = world.rider(rid)
        except StopIteration:
            continue                      # 赛季结束后退役了
        shown += 1
        print(f"  {shown}  {r.name:<20}"
              f"{world.team(r.team_id).name.split('-')[0]:<8}"
              f"{r.age}岁  {r.role.value:<11}总评 {r.overall:<4}"
              f"潜力 {r.potential}")
        if shown >= 8:
            break

    print("\n八年后涌现的新星（26 岁以下、总评 78 以上）：")
    rookies = sorted((r for r in world.riders if r.age <= 26 and r.overall >= 78),
                     key=lambda r: -r.overall)[:6]
    for r in rookies:
        print(f"  {r.name:<20}{r.age}岁  {r.role.value:<11}"
              f"总评 {r.overall}  潜力 {r.potential}  "
              f"{world.team(r.team_id).name.split('-')[0]}")
    if not rookies:
        print("  （没有：说明成长系统太保守，年轻人练不出来）")


if __name__ == "__main__":
    main()
