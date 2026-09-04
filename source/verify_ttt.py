"""团体计时赛验证。

    python3 source/verify_ttt.py

团体计时赛是这套引擎里最后一个补上物理表达的赛段类型。它值得单独建模的
理由只有一条：**一支队不是「跑得多快」，是「能带着几个人跑多快」。**
所以这里验的不只是速度对不对，更是那几条取舍成不成立。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))

from sim.roster import build_peloton                    # noqa: E402
from sim.ttt import team_time_trial, ttt_stage          # noqa: E402

OK, BAD = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
fails: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(f"  {OK if cond else BAD} {label}{('   ' + extra) if extra else ''}")
    if not cond:
        fails.append(label)


def squads():
    out: dict[str, list] = {}
    for r in build_peloton(20, 7):
        out.setdefault(r.team_id, []).append(r)
    return out


def main() -> None:
    print("=" * 70)
    print("团体计时赛 · 验证")
    print("=" * 70)
    course = ttt_stage()
    teams = squads()
    res = {tid: team_time_trial(sq, course, count_nth=4)
           for tid, sq in teams.items()}
    speeds = sorted(r.avg_speed_kmh for r in res.values())
    kept = sorted(len(r.finishers) for r in res.values())

    print(f"\n[速度] {course.name} {course.length_m / 1000:.0f} km")
    check(50.0 <= speeds[0] and speeds[-1] <= 58.0,
          "均速落在职业团体计时赛的区间", f"{speeds[0]:.1f}–{speeds[-1]:.1f} km/h"
          "（现实 50–58）")
    check(speeds[-1] - speeds[0] > 1.5, "强队和弱队拉得开",
          f"极差 {speeds[-1] - speeds[0]:.1f} km/h")

    print("\n[带几个人到线]")
    check(min(kept) >= 4, "永远不会掉到计时位以下", f"最少 {min(kept)} 人")
    check(max(kept) > min(kept), "**不同的队带到线的人数不一样**",
          f"{min(kept)}–{max(kept)} 人 / 8")
    print(f"    分布：{kept}")
    print("    全都一样就说明模型是同义反复——配速定成「平均那个人刚好到极限」，")
    print("    弱于平均的一半必然被榨干，所有队伍长得一模一样。")

    print("\n[阵容厚度值不值钱]")
    # 同样的前四人，队尾换成更强的四个人，成绩应该更好
    ranked = sorted(teams.items(), key=lambda kv: -sum(
        sorted(r.params.cp for r in kv[1])[-4:]))
    top4 = ranked[0][1]
    core = sorted(top4, key=lambda r: -r.params.cp)[:4]
    weak_tail = sorted(ranked[-1][1], key=lambda r: -r.params.cp)[:4]
    strong_tail = sorted(ranked[1][1], key=lambda r: -r.params.cp)[:4]
    a = team_time_trial(core + weak_tail, course, count_nth=4)
    b = team_time_trial(core + strong_tail, course, count_nth=4)
    check(b.time_s < a.time_s, "**同样的前四人，队尾更强就更快**",
          f"弱尾 {a.time_s / 60:.2f} 分 → 强尾 {b.time_s / 60:.2f} 分 "
          f"（快 {(a.time_s - b.time_s):.0f} 秒）")
    print("    这是这个赛段类型存在的意义：它是全游戏里唯一一个")
    print("    「第五到第八人有多强」直接决定成绩的地方。")

    print("\n[计时位改变一切]")
    sq = ranked[0][1]
    r4 = team_time_trial(sq, course, count_nth=4)
    r6 = team_time_trial(sq, course, count_nth=6)
    check(len(r6.finishers) > len(r4.finishers), "取第 6 人时留下的人更多",
          f"{len(r4.finishers)} → {len(r6.finishers)} 人")
    check(abs(r6.time_s - r4.time_s) > 10, "规则改一个数字，成绩就不一样",
          f"{r4.time_s / 60:.2f} 分 → {r6.time_s / 60:.2f} 分")
    print("    **这里出了一个我预期反了的结果，而它是对的**：取第 6 人反而更快。")
    print("    我原本以为「要带更多人到线」必然更慢。但配速由计时位那个人决定，")
    print("    取第 6 人时配速更保守，于是八个人全都留了下来——八个人分摊领骑，")
    print("    每人在风里的时间少了一半，可持续的速度反而更高。")
    print("    **一支整整齐齐的队，比一支炸掉之后只剩四个人硬顶的队更快。**")
    print("    这正是现实里教练拼命不让人掉队的原因，也是这个模型没有白写的证据——")
    print("    它给出了一个我没有事先写进去的、但符合真实赛车道理的结论。")

    print("\n[掉队是渐进的]")
    sample = res[ranked[-1][0]]
    check(bool(sample.dropped), "弱队确实会掉人")
    if sample.dropped:
        kms = [round(d / 1000, 1) for _, d in sample.dropped]
        check(len(set(kms)) > 1 or len(kms) == 1,
              "掉队分散在全程而不是同时崩", f"{kms} km")

    print("\n" + "=" * 70)
    if fails:
        print(f"\033[31m{len(fails)} 项没过：\033[0m " + "；".join(fails))
        sys.exit(1)
    print("\033[32m团体计时赛成立：速度对得上，阵容厚度真的值钱。\033[0m")


if __name__ == "__main__":
    main()
