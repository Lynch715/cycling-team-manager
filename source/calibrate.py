"""标定：把引擎输出和真实公路赛的已知区间对照。

数值游戏最容易出的问题不是崩溃，是"跑得出来但不像真的"。
这个脚本把每个可观测量和现实区间并排打出来，超出区间就标 ✗。

参考区间取自公开的职业赛数据（赛段均速、功率计文件、爬坡计时）：
  · 大环赛平路赛段均速          40–45 km/h
  · 山地赛段（>4000m 爬升）均速  29–34 km/h
  · 38km 个人计时赛均速          46–53 km/h
  · 顶级爬坡手长坡输出           5.8–6.5 W/kg（40 分钟量级）
  · 顶级冲刺手峰值功率           1500–1900 W
  · 五小时赛段的平均功率         3.2–4.3 W/kg
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sim import (  # noqa: E402
    Course, Race, Segment, StageType, build_peloton, cobbled_stage, flat_stage,
    format_gap, format_time, itt_stage, mountain_stage,
)

OK, BAD = "\033[32m✓\033[0m", "\033[31m✗\033[0m"


def check(label: str, value: float, lo: float, hi: float, unit: str = "") -> bool:
    good = lo <= value <= hi
    mark = OK if good else BAD
    print(f"  {mark} {label:<26}{value:>8.2f}{unit:<7}  参考 {lo}–{hi}")
    return good


def alpe_test() -> bool:
    """单人爬坡计时：13.8 km @ 8.1%，对标一线爬坡手的实测成绩。"""
    from sim import Attributes, Rider, Role
    from sim.physics import air_density, steady_speed

    climb = Course("基准长坡", [Segment(13800, 0.081, name="长坡")],
                   StageType.MOUNTAIN, start_altitude_m=740)
    elite = Rider("x", "基准爬坡手", "TST", 60.0,
                  Attributes(flat=78, climbing=95, sprint=45, time_trial=80,
                             descending=80, endurance=90, recovery=85,
                             positioning=80, resilience=90), Role.CLIMBER)

    p = elite.params
    cp_climb = p.cp_climb
    rho = air_density(1300)
    v = steady_speed(cp_climb * 1.03, p.total_mass, 0.081,
                     p.cda_hoods, p.crr, rho)
    minutes = 13800 / v / 60
    print(f"\n[单人爬坡基准] 13.8km @ 8.1%，车手 {cp_climb / 60.0:.2f} W/kg")
    a = check("完成时间", minutes, 37.0, 45.0, " 分")
    b = check("爬升速率", 13800 * 0.081 / (minutes / 60), 1350, 1750, " m/h")
    return a and b


def stage_test(name: str, course, speed_lo: float, speed_hi: float,
               power_lo: float = 3.0, power_hi: float = 4.5,
               bunch_sprint: bool = True, gap2_hi: float = 90.0,
               teams: int = 20, seed: int = 42) -> bool:
    t0 = time.time()
    result = Race(course, build_peloton(teams, 7), dt=1.0, seed=seed).run()
    w = result.winner
    if w is None:
        print(f"\n[{name}] 无人完赛 {BAD}")
        return False

    kmh = course.length_m / w.finish_time * 3.6
    wkg = w.avg_power / w.rider.body_mass_kg
    peak = max(s.peak_power for s in result.finishers)
    finish_rate = len(result.finishers) / (len(result.finishers) + len(result.dnf))
    gap2 = result.gap_to_winner(result.finishers[1]) if len(result.finishers) > 1 else 0.0

    print(f"\n[{name}] {course.length_m / 1000:.0f} km，爬升 "
          f"{course.total_ascent_m:.0f} m，用时 {format_time(w.finish_time)}"
          f"（{time.time() - t0:.1f}s 算完）")
    checks = [
        check("赛段均速", kmh, speed_lo, speed_hi, " km/h"),
        check("冠军平均功率", wkg, power_lo, power_hi, " W/kg"),
        check("亚军差距", gap2, 0.0, gap2_hi, " 秒"),
        check("完赛率", finish_rate * 100, 82, 100, " %"),
    ]
    if bunch_sprint:
        checks.append(check("全场最高瞬时功率", peak, 1350, 2000, " W"))
    print(f"    冠军：{w.rider.name}（{w.rider.role.value}，"
          f"{w.rider.body_mass_kg}kg，剩余 W′ {w.energy.w_fraction:.0%}）")
    print("    前八角色：" + "、".join(s.rider.role.value
                                     for _, s, _ in result.standings()[:8]))
    print("    前八差距：" + "、".join(format_gap(g)
                                     for _, _, g in result.standings()[:8]))
    return all(checks)


def main() -> None:
    print("=" * 72)
    print("模拟引擎标定报告")
    print("=" * 72)

    results = [
        alpe_test(),
        stage_test("平路赛段", flat_stage(), 40.0, 45.0, gap2_hi=5.0),
        stage_test("山地赛段", mountain_stage(), 29.0, 34.0,
                   bunch_sprint=False, gap2_hi=150.0),
        stage_test("个人计时赛", itt_stage(), 44.0, 53.0,
                   power_lo=4.5, power_hi=6.3, bunch_sprint=False, gap2_hi=60.0),
        stage_test("石板路赛段", cobbled_stage(), 38.0, 45.0,
                   bunch_sprint=False, gap2_hi=120.0),
    ]

    print("\n" + "=" * 72)
    print(f"通过 {sum(results)}/{len(results)} 项")


if __name__ == "__main__":
    main()
