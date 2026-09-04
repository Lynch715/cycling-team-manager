"""战术验证：玩家选的打法，在一个赛季的尺度上到底值多少。

    python3 source/verify_tactics.py

`verify_orders.py` 早就证明了指令能改变**单场比赛**的结果——但那测的是
完整引擎，而赛季里跑的是快速结算。两者之间断了一根线：career 每场比赛都
算好一份 orders，然后原样扔掉，`run_event` 根本没有这个参数。**玩家唯一
的战术杠杆，在整个生涯模式里是空的。** 这个脚本就是那根线的看门人。

它做两件事：

一、把八条指令的数值含义摊开。玩家在界面上看到「护航队长」四个字，
    这里显示的是它实际扣了多少、补了多少。

二、同一个种子、同一支队，五种打法各跑一个完整赛季，比结果。
    如果五行数字一模一样，说明线又断了。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))

from game.career import new_career                          # noqa: E402
from game.orders import EFFECTS, Order, PLAYBOOKS           # noqa: E402
from game.quickresolve import (CONSERVE_COST, PULL_COST,    # noqa: E402
                               SHELTER_CAP, SHELTER_GAIN,
                               SPEND_GAIN, SPRINT_SWING,
                               _is_protected)

OK, BAD = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def order_table() -> None:
    print("=" * 74)
    print("八条指令的数值含义")
    print("=" * 74)
    print("  能力增减 = 领骑代价 + 舍得烧 − 保存体力；掩护收益按车队另算\n")
    print(f"  {'指令':<12}{'领骑':>6}{'进攻':>6}{'冲刺':>6}"
          f"{'能力增减':>10}{'冲刺增减':>10}  被保护")
    for o in Order:
        d = EFFECTS[o]
        m = (-PULL_COST * (d.pull_bias - 1.0)
             + SPEND_GAIN * (d.spend_bias - 1.0)
             - CONSERVE_COST * d.conserve)
        s = SPRINT_SWING * (d.sprint_bias - 1.0)
        print(f"  {o.value:<14}{d.pull_bias:>5.2f}{d.attack_bias:>6.1f}"
              f"{d.sprint_bias:>6.2f}{m * 100:>+9.2f}%{s * 100:>+9.1f}%"
              f"   {'是' if _is_protected(d) else ''}")
    top = SHELTER_GAIN * SHELTER_CAP * 100
    print(f"\n  掩护收益封顶 {top:+.1f}%（队友顶的风加起来，保护两个人就每人一半）")
    print("  参照：能力差 1% 在自行车里就是慢 1%，一场五小时的比赛里是三分钟。")


def season_table(seasons: int = 1) -> bool:
    print("\n" + "=" * 74)
    print(f"五种打法各跑 {seasons} 个赛季（同一个种子、同一支队）")
    print("=" * 74)
    rows = []
    for pb in PLAYBOOKS:
        c = new_career("T08", 2026)
        c.advance()                       # 排队的第一件事就是选打法
        c.resolve(0, list(PLAYBOOKS).index(pb))
        for _ in range(seasons):
            c.play_season(auto=True)
        wins = sum(1 for r in c.season_results if r["best_place"] == 1)
        top3 = sum(1 for r in c.season_results
                   if r["best_place"] and r["best_place"] <= 3)
        top10 = sum(1 for r in c.season_results
                    if r["best_place"] and r["best_place"] <= 10)
        pts = sum(p for rid, p in c._points.items()
                  if any(x.rider_id == rid for x in c.roster))
        avg = sum(r["best_place"] for r in c.season_results
                  if r["best_place"]) / max(1, len(c.season_results))
        rows.append((pb, wins, top3, top10, pts, avg))

    print(f"\n  {'打法':<12}{'夺冠':>6}{'前三':>6}{'前十':>6}"
          f"{'本队积分':>10}{'平均最好名次':>14}")
    for pb, w, t3, t10, pts, avg in rows:
        print(f"  {pb:<14}{w:>5}{t3:>6}{t10:>6}{pts:>10}{avg:>13.1f}")

    print()
    spread = max(r[3] for r in rows) - min(r[3] for r in rows)
    same = len({(r[1], r[2], r[3], r[4]) for r in rows}) == 1
    ok = not same
    print(f"  {OK if ok else BAD} 五种打法给出了不同的赛季结果"
          f"{'' if ok else'  ← 指令又断线了，检查 run_event 的 orders 参数'}")
    print(f"  {OK if spread >= 2 else BAD} 差异足够大  前十次数极差 {spread}")

    best = max(rows, key=lambda r: r[3])
    worst = min(rows, key=lambda r: r[3])
    print(f"\n  这支队最适合「{best[0]}」（前十 {best[3]} 次），"
          f"最不适合「{worst[0]}」（{worst[3]} 次）。")
    print("  差别来自阵容构成——没有哪套打法是普遍最优的，这正是要的效果。")
    return ok and spread >= 2


def main() -> None:
    order_table()
    ok = season_table()
    print("\n" + "=" * 74)
    if not ok:
        sys.exit(1)
    print("\033[32m战术是活的：玩家选的打法确实改变了赛季结果。\033[0m")


if __name__ == "__main__":
    main()
