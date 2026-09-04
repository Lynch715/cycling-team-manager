"""跑一整个赛季，或单独跑一场赛事。

    python3 source/run_season.py                      # 整季（快速结算，几秒）
    python3 source/run_season.py --event E08          # 只跑五月大环赛
    python3 source/run_season.py --event E03 --full   # 用完整引擎逐秒跑
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game.generate_world import generate  # noqa: E402
from game.season import run_event, run_season  # noqa: E402
from game.world import World  # noqa: E402
from sim.race import format_gap, format_time  # noqa: E402

JERSEY_LABEL = {
    "leader-jersey-01": "总成绩", "leader-jersey-02": "冲刺积分",
    "leader-jersey-03": "爬坡积分", "leader-jersey-04": "最佳新秀",
}


def load_world(path: str) -> World:
    p = Path(__file__).resolve().parents[1] / path
    return World.load(p) if p.exists() else generate()


def show_event(world: World, outcome) -> None:
    e = outcome.event
    print(f"\n{'=' * 70}\n{e.name}  ({e.tier.value}, {e.days} 天, "
          f"声望 {e.prestige})\n{'=' * 70}")

    gc = outcome.classification.gc_standings()
    lead = gc[0][1]
    print(f"\n{'名次':<5}{'车手':<20}{'车队':<8}{'国籍':<6}{'成绩/差距':<12}")
    print("-" * 62)
    for i, (rid, t) in enumerate(gc[:10], start=1):
        r = world.rider(rid)
        tm = world.team(r.team_id)
        label = format_time(t) if i == 1 else format_gap(t - lead)
        print(f"{i:<5}{r.name:<20}{tm.short_name:<8}{r.nation:<6}{label:<12}")

    print("\n各项分类领骑衫：")
    ages = {r.rider_id: r.age for r in world.riders}
    for art, rid in outcome.classification.jerseys(ages).items():
        r = world.rider(rid)
        print(f"  {JERSEY_LABEL[art]:<6}{r.name:<20}"
              f"（{world.team(r.team_id).short_name}，{art}）")

    if e.is_stage_race:
        print("\n赛段冠军：")
        for st in outcome.stages:
            if st.winner:
                r = world.rider(st.winner)
                print(f"  第 {st.stage_index + 1:>2} 赛段 "
                      f"[{st.stage_type:<14}] {r.name}"
                      f"（{world.team(r.team_id).short_name}）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default="data/world.json")
    ap.add_argument("--event", default=None, help="只跑指定赛事 id，如 E08")
    ap.add_argument("--full", action="store_true", help="用完整引擎逐秒模拟")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    world = load_world(args.world)
    engine = "full" if args.full else "quick"
    t0 = time.time()

    if args.event:
        event = next(e for e in world.calendar if e.race_id == args.event)
        outcome = run_event(world, event, random.Random(args.seed), engine)
        show_event(world, outcome)
        print(f"\n用时 {time.time() - t0:.1f} 秒"
              f"（{'完整引擎' if args.full else '快速结算'}，"
              f"{event.days} 个比赛日）")
        return

    season = run_season(world, seed=args.seed, engine=engine)
    print(f"赛季 {world.season} 共 {len(season.events)} 场赛事、"
          f"{sum(e.event.days for e in season.events)} 个比赛日，"
          f"{time.time() - t0:.1f} 秒跑完")

    print("\n六大赛冠军：")
    for out in season.events:
        if out.event.tier.value in ("大环赛", "纪念碑"):
            rid = out.winner
            r = world.rider(rid)
            print(f"  {out.event.name:<12}{r.name:<20}"
                  f"{world.team(r.team_id).short_name:<6}{r.nation}")

    print("\n赛季车手排名前十五：")
    print(f"{'名次':<5}{'车手':<20}{'车队':<8}{'角色':<11}{'年龄':<5}"
          f"{'总评':<5}{'积分':>7}")
    print("-" * 66)
    for i, (rid, pts) in enumerate(season.rider_ranking()[:15], start=1):
        r = world.rider(rid)
        print(f"{i:<5}{r.name:<20}{world.team(r.team_id).short_name:<8}"
              f"{r.role.value:<11}{r.age:<5}{r.overall:<5}{pts:>7}")

    print("\n赛季车队排名：")
    for i, (tid, pts) in enumerate(season.team_ranking()[:10], start=1):
        t = world.team(tid)
        print(f"  {i:>2}  {t.name:<22}{t.division.value:<6}{pts:>8} 分")

    print("\n赛季结束时的疲劳（前五名最累的车手）：")
    for r in sorted(world.riders, key=lambda r: -r.fatigue)[:5]:
        print(f"  {r.name:<20}疲劳 {r.fatigue:.0%}  恢复属性 "
              f"{r.attributes.recovery}")


if __name__ == "__main__":
    main()
