"""历史报告：把成绩库里的东西读出来给人看。

两种用法，对应两种人：

    python3 source/history.py                 玩家视角：名人堂、赛道纪录、王朝
    python3 source/history.py --balance       设计者视角：地形×角色矩阵、夺冠年龄
    python3 source/history.py --sim 25        先空跑 25 个赛季再出报告

第三种是这套东西真正的价值所在。**平衡问题在单个赛季里是看不见的**——
一个赛季里冲刺手赢了十场，你说不出那是设计如此还是数值崩了；
二十五个赛季堆起来，「丘陵赛段 66% 被纯冲刺手拿下」这种句子才会浮出来，
而它一旦浮出来就是一句明确的、可以照着改的结论。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game.records import (career_wins, connect, course_records,  # noqa: E402
                          dynasty_check, age_of_winners, nation_medals,
                          gc_by_role, role_balance, summary,
                          wins_by_stage_type, world_level)

BAR = "=" * 72


def _hms(sec: float | None) -> str:
    if sec is None:
        return "—"
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def simulate(seasons: int, db_path: Path, team_id: str = "T08",
             start: int = 2026):
    from game.career import new_career
    car = new_career(team_id, start)
    car.attach_db(db_path)
    for _ in range(seasons):
        car.play_season(auto=True)
    return car.db


# --------------------------------------------------------------------------
# 玩家视角
# --------------------------------------------------------------------------

def player_report(conn) -> None:
    s = summary(conn)
    print(BAR)
    print(f"历史档案 · {s['seasons']} 个赛季 · {s['rows']} 条成绩 · "
          f"{s['riders']} 名车手留下过名字")
    print(BAR)

    print("\n【名人堂】按大赛冠军数排序")
    print(f"  {'车手':<22}{'总冠军':>7}{'大赛':>7}")
    for r in career_wins(conn, 12):
        print(f"  {r['rider_name']:<24}{r['wins']:>5}{r['big']:>7}")

    print("\n【赛道纪录】")
    for r in course_records(conn, 10):
        print(f"  {r['race_name'][:18]:<20}{_hms(r['best']):>10}   "
              f"{r['rider_name']} ({r['season']})")

    print("\n【车队王朝】拿过年度第一的队")
    for r in dynasty_check(conn)[:8]:
        print(f"  {r['team_name'][:22]:<24}{r['titles']:>3} 次")

    print("\n【车手国籍】胜场分布")
    row = "  " + "   ".join(f"{r['nation']} {r['wins']}"
                            for r in nation_medals(conn, 8))
    print(row)


# --------------------------------------------------------------------------
# 设计者视角
# --------------------------------------------------------------------------

def balance_report(conn) -> None:
    print(BAR)
    print("平衡诊断")
    print(BAR)

    print("\n【角色产出】per100 = 每一百个「车手赛季」赢几场")
    print("  原始胜场数没有意义——工兵占了半个车队，胜场当然多。")
    print("  除以人数之后才回答了玩家真正在问的问题：签哪种车手划算。\n")
    print(f"  {'角色':<12}{'人数':>6}{'胜场':>6}{'per100':>8}"
          f"{'总成绩':>7}{'大环赛':>7}{'纪念碑':>7}")
    for r in role_balance(conn):
        print(f"  {r['role']:<14}{r['pop']:>5}{r['wins']:>6}"
              f"{r['per100']:>8}{r['overalls']:>7}{r['gt']:>7}{r['mon']:>7}")

    print("\n【地形筛选】每种赛段的冠军由谁拿走（%）")
    print("  这是最硬的一张表。如果山地赛段的冠军里冲刺手占了两成，")
    print("  说明地形根本没有在筛人，赛道设计对结果不起作用。\n")
    for r in wins_by_stage_type(conn):
        top = "  ".join(f"{k} {v}%" for k, v in list(r["shares"].items())[:4])
        print(f"  {r['stage_type']:<15}{r['total']:>5} 场   {top}")

    print("\n【大环赛总成绩】按人均，绝对数会骗人")
    print("  每支队只有一名总成绩核心，却有两名爬坡手——爬坡手的绝对夺冠数")
    print("  天然是两倍。只有除以人数，才知道这个角色称不称职。\n")
    print(f"  {'角色':<12}{'人数':>6}{'夺冠':>6}{'每百人':>9}")
    for r in gc_by_role(conn):
        print(f"  {r['role']:<14}{r['pop']:>5}{r['wins']:>6}{r['per100']:>9}")

    print("\n【夺冠年龄】真实公路车集中在 26-31 岁")
    for r in age_of_winners(conn):
        bar = "█" * max(1, r["wins"] // 12)
        print(f"  {r['band']:<7}{r['wins']:>5}  {bar}")

    print("\n【世界水平】每季前 20 名的平均总评，应当平稳")
    lv = world_level(conn)
    if lv:
        vals = [r["level"] for r in lv]
        lo, hi = min(vals), max(vals)
        for r in lv:
            n = int(round((r["level"] - lo) / max(0.1, hi - lo) * 30))
            print(f"  {r['season']}  {r['level']:>5}  {'·' * n}")
        print(f"  波动范围 {lo} – {hi}（{hi - lo:.1f} 分）"
              f"{'  ← 偏大，稳态控制在震荡' if hi - lo > 3.0 else '  ← 正常'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="历史与平衡报告")
    ap.add_argument("--db", default=None, help="成绩库路径")
    ap.add_argument("--sim", type=int, default=0, help="先空跑 N 个赛季")
    ap.add_argument("--balance", action="store_true", help="只出平衡诊断")
    args = ap.parse_args()

    path = Path(args.db) if args.db else None
    if args.sim:
        path = path or Path("data/records.db")
        print(f"空跑 {args.sim} 个赛季……")
        conn = simulate(args.sim, path)
    else:
        conn = connect(path)

    if summary(conn)["rows"] == 0:
        print("成绩库是空的。先跑：python3 source/history.py --sim 25")
        return

    if args.balance:
        balance_report(conn)
    else:
        player_report(conn)
        print()
        balance_report(conn)


if __name__ == "__main__":
    main()
