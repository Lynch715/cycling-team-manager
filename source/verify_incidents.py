"""验证：摔车与机械故障的发生率、成因、后果是否都站得住。

    python3 source/verify_incidents.py

查四件事，每一件对应一种做砸的方式：

  1. **发生率对不对** —— 太高会让玩家觉得被耍，太低等于没做。
     真实公路赛：干燥天约 5-10% 的车手会遇到状况，雨天和石板路翻倍。
  2. **成因是不是玩家能影响的** —— 石板路、雨天、技术性下坡必须明显更危险。
     如果各种条件下概率都差不多，那它就是个纯骰子。
  3. **在集团里的位置有没有用** —— 埋得越深越省力也越容易被连环摔波及。
     这是全游戏最漂亮的权衡，必须能量到。
  4. **后果是不是以损失时间为主** —— 退赛应当是少数。一上来就让人退赛，
     玩家只会觉得系统在惩罚他。
"""

from __future__ import annotations

import random
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sim import Race, build_peloton, cobbled_stage, flat_stage, mountain_stage  # noqa: E402
from sim.course import Surface  # noqa: E402
from sim.incidents import IncidentKind, crash_hazard  # noqa: E402

OK, BAD = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def check(label: str, value: float, lo: float, hi: float, unit: str = "") -> bool:
    good = lo <= value <= hi
    print(f"  {OK if good else BAD} {label:<24}{value:>7.1f}{unit:<5}"
          f"  参考 {lo}–{hi}")
    return good


def run(course_fn, rain: float, teams: int = 6, seed: int = 42):
    r = Race(course_fn(), build_peloton(teams, 7), seed=seed, rain=rain).run()
    allst = r.finishers + r.dnf
    with_inc = [s for s in allst if s.incidents]
    kinds = Counter(i.kind for s in allst for i in s.incidents)
    losses = [i.lost_seconds for s in allst for i in s.incidents
              if i.lost_seconds > 0]
    return dict(
        n=len(allst),
        rate=len(with_inc) / max(1, len(allst)) * 100,
        kinds=kinds,
        abandons=kinds[IncidentKind.ABANDON],
        median_loss=statistics.median(losses) if losses else 0.0,
    )


def main() -> None:
    print("=" * 72)
    print("摔车与机械故障验证")
    print("=" * 72)

    results = []

    print("\n[一] 发生率随条件变化")
    dry = run(flat_stage, 0.0)
    wet = run(flat_stage, 0.85)
    cob = run(cobbled_stage, 0.0)
    mtn = run(mountain_stage, 0.0)
    results.append(check("干燥平路赛段", dry["rate"], 3.0, 12.0, " %"))
    results.append(check("雨天平路赛段", wet["rate"], 10.0, 30.0, " %"))
    results.append(check("干燥石板路赛段", cob["rate"], 8.0, 28.0, " %"))
    results.append(check("山地赛段", mtn["rate"], 3.0, 15.0, " %"))

    print("\n[二] 条件确实在起作用（不是纯骰子）")
    ok = wet["rate"] > dry["rate"] * 1.5
    print(f"  {OK if ok else BAD} 雨天风险是干燥的 "
          f"{wet['rate'] / max(dry['rate'], 0.1):.1f} 倍　参考 >1.5")
    results.append(ok)
    ok = cob["rate"] > dry["rate"] * 1.3
    print(f"  {OK if ok else BAD} 石板路风险是柏油路的 "
          f"{cob['rate'] / max(dry['rate'], 0.1):.1f} 倍　参考 >1.3")
    results.append(ok)

    print("\n[三] 在集团里的位置：省力与安全的矛盾")
    base = dict(speed=13.0, grade=-0.05, surface=Surface.ASPHALT,
                technical=0.5, group_size=100, descending=60,
                w_fraction=0.6, rain=0.0)
    front = crash_hazard(**{**base, "draft_rank": 3})
    deep = crash_hazard(**{**base, "draft_rank": 70})
    ok = deep > front * 1.5
    print(f"  {OK if ok else BAD} 埋在集团深处的风险是前排的 "
          f"{deep / front:.1f} 倍　参考 >1.5")
    results.append(ok)

    good_desc = crash_hazard(**{**base, "draft_rank": 20, "descending": 90})
    bad_desc = crash_hazard(**{**base, "draft_rank": 20, "descending": 30})
    ok = bad_desc > good_desc * 1.3
    print(f"  {OK if ok else BAD} 下坡属性 30 比 90 危险 "
          f"{bad_desc / good_desc:.1f} 倍　参考 >1.3")
    results.append(ok)

    fresh = crash_hazard(**{**base, "draft_rank": 20, "w_fraction": 1.0})
    empty = crash_hazard(**{**base, "draft_rank": 20, "w_fraction": 0.0})
    ok = empty > fresh * 1.4
    print(f"  {OK if ok else BAD} 储备见底比满储备危险 "
          f"{empty / fresh:.1f} 倍　参考 >1.4")
    results.append(ok)

    print("\n[四] 后果以损失时间为主，退赛是少数")
    total_inc = sum(sum(v for v in r["kinds"].values())
                    for r in (dry, wet, cob, mtn))
    total_ab = sum(r["abandons"] for r in (dry, wet, cob, mtn))
    ab_share = total_ab / max(1, total_inc) * 100
    results.append(check("退赛占全部意外的比例", ab_share, 0.0, 8.0, " %"))
    results.append(check("时间损失中位数", dry["median_loss"], 15.0, 120.0, " 秒"))

    print("\n各类意外的分布（四场比赛合计）：")
    merged: Counter = Counter()
    for r in (dry, wet, cob, mtn):
        merged.update(r["kinds"])
    for k, v in merged.most_common():
        print(f"  {k.value:<10}{v:>3} 次")

    print("\n" + "=" * 72)
    print(f"通过 {sum(results)}/{len(results)} 项")


if __name__ == "__main__":
    main()
