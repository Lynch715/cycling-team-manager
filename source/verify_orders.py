"""验证：赛前指令确实改变比赛结果，而且改变的方向是对的。

    python3 source/verify_orders.py --stage flat --runs 5

这个脚本存在的理由是防止一种最难发现的失败：指令看起来有效果，
其实只是随机噪声。做法是同一批车手、同一条赛道，只改一支队的指令，
跑多个随机种子取平均，然后看两件事：

  1. **有没有区别** —— 不同打法下这支队的最好名次是否显著不同
  2. **方向对不对** —— 平路赛段上"冲刺夺段"应当优于"全员抢突围"，
     山地赛段上应当反过来

如果两条都不成立，那战术层就是个装饰性的下拉菜单，应当推倒重做。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game.orders import PLAYBOOKS, apply_playbook, build_directives  # noqa: E402
from sim import Race, build_peloton, cobbled_stage, flat_stage, mountain_stage  # noqa: E402

STAGES = {"flat": flat_stage, "mountain": mountain_stage,
          "cobbled": cobbled_stage}


def run_with(playbook: str | None, course_fn, seed: int,
             team: str = "T05", teams: int = 10):
    riders = build_peloton(teams, 7)
    squad = [r for r in riders if r.team_id == team]
    directives = None
    if playbook:
        orders = apply_playbook(playbook, squad)
        directives = build_directives(squad, orders)
    result = Race(course_fn(), riders, dt=1.0, seed=seed,
                  directives=directives).run()

    places = {s.rider.rider_id: i + 1
              for i, s in enumerate(result.finishers)}
    ours = [places.get(r.rider_id, 999) for r in squad]
    top10 = sum(1 for p in ours if p <= 10)
    return min(ours), top10, result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="flat", choices=sorted(STAGES))
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--teams", type=int, default=10)
    ap.add_argument("--team", default="T05",
                    help="被测队伍。刻意选中游队：顶级队不下指令也会赢，"
                         "测不出战术的价值")
    ap.add_argument("--books", default="",
                    help="只测这几种打法，逗号分隔")
    args = ap.parse_args()

    course_fn = STAGES[args.stage]
    books = [None] + (args.books.split(",") if args.books else list(PLAYBOOKS))

    print("=" * 70)
    print(f"赛前指令有效性验证 · {args.stage} 赛段 · {args.runs} 个随机种子")
    print("=" * 70)
    print(f"\n{'打法':<12}{'最好名次(均)':<14}{'前十人次(均)':<14}{'各次最好名次'}")
    print("-" * 70)

    summary = {}
    for book in books:
        bests, tens, raw = [], [], []
        for i in range(args.runs):
            best, top10, _ = run_with(book, course_fn, seed=100 + i,
                                      team=args.team, teams=args.teams)
            bests.append(best)
            tens.append(top10)
            raw.append(best)
        label = book or "（不下指令）"
        summary[label] = statistics.mean(bests)
        print(f"{label:<12}{statistics.mean(bests):<14.1f}"
              f"{statistics.mean(tens):<14.1f}{raw}")

    ordered = sorted(summary.items(), key=lambda x: x[1])
    spread = ordered[-1][1] - ordered[0][1]
    print(f"\n最好的打法：{ordered[0][0]}（平均最好名次 {ordered[0][1]:.1f}）")
    print(f"最差的打法：{ordered[-1][0]}（平均最好名次 {ordered[-1][1]:.1f}）")
    print(f"打法之间的名次跨度：{spread:.1f} 位")

    if spread < 2.0:
        print("\n✗ 跨度太小——指令没有真实影响，战术层需要返工")
    else:
        print("\n✓ 不同打法带来了显著不同的结果，玩家的决定有后果")


if __name__ == "__main__":
    main()
