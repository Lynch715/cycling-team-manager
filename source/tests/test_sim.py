"""引擎单元测试。

    python3 -m pytest source/tests -q
    python3 source/tests/test_sim.py      # 无 pytest 时也能跑

测试分三层：物理层验证方程本身没写错（可以和手算对上），
属性层验证策划改数值时的方向性（提高爬坡属性必须让人爬得更快），
比赛层验证涌现行为（跟车真的省力、掉队真的会发生、结果可复现）。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim import (  # noqa: E402
    Attributes, Race, Rider, Role, Segment, Course, StageType,
    build_peloton, draft_factor, flat_stage, form_groups, itt_stage,
    mountain_stage,
)
from sim.energy import EnergyState, recovery_tau  # noqa: E402
from sim.pack import bunch_depth, echelon_capacity  # noqa: E402
from sim.physics import (  # noqa: E402
    air_density, advance_speed, power_required, steady_speed,
)
from sim.rider import derive_params  # noqa: E402


# --------------------------------------------------------------------------
# 物理层
# --------------------------------------------------------------------------

def test_flat_power_matches_hand_calculation():
    """平路 45 km/h 所需功率应落在职业车手独走的已知区间。"""
    v = 45 / 3.6
    p = power_required(v, total_mass=78.0, grade=0.0, cda=0.32, crr=0.004)
    assert 380 < p < 460, p


def test_climb_power_dominated_by_gravity():
    """8% 坡上 15 km/h，功率应几乎全部用于对抗重力。"""
    v = 15 / 3.6
    p = power_required(v, total_mass=68.0, grade=0.08, cda=0.32, crr=0.004)
    gravity_only = 68.0 * 9.80665 * math.sin(math.atan(0.08)) * v
    assert 0.82 < gravity_only / p < 0.95, gravity_only / p


def test_steady_speed_is_inverse_of_power_required():
    for power in (150.0, 300.0, 450.0):
        for grade in (-0.05, 0.0, 0.06):
            v = steady_speed(power, 78.0, grade, 0.32, 0.004)
            back = power_required(v, 78.0, grade, 0.32, 0.004)
            assert abs(back - power) < 1.0 or v >= 44.9, (power, grade, v, back)


def test_descent_reaches_terminal_speed_without_pedalling():
    """不蹬踏的 8% 下坡，终端速度应在 60-90 km/h 量级。"""
    v = steady_speed(0.0, 78.0, -0.08, 0.30, 0.004)
    assert 16 < v < 25, v * 3.6


def test_acceleration_converges_to_steady_speed():
    target = steady_speed(300.0, 78.0, 0.0, 0.32, 0.004)
    v = 5.0
    for _ in range(400):
        v = advance_speed(v, 300.0, 1.0, 78.0, 0.0, 0.32, 0.004)
    assert abs(v - target) < 0.05, (v, target)


def test_air_density_drops_with_altitude():
    assert air_density(0) > air_density(1000) > air_density(2500)
    # 国际标准大气：海拔 2500m 空气密度约为海平面的 78%
    assert 0.74 < air_density(2500) / air_density(0) < 0.82


def test_tailwind_helps_headwind_hurts():
    base = power_required(11.0, 78.0, 0.0, 0.32, 0.004)
    head = power_required(11.0, 78.0, 0.0, 0.32, 0.004, headwind=4.0)
    tail = power_required(11.0, 78.0, 0.0, 0.32, 0.004, headwind=-4.0)
    assert tail < base < head


# --------------------------------------------------------------------------
# 能量层
# --------------------------------------------------------------------------

def test_w_prime_depletes_above_cp_and_refills_below():
    e = EnergyState(cp=300.0, w_prime=20000.0)
    e.update(400.0, 60.0)                       # 超 CP 100W 骑 60 秒
    assert abs(e.w_bal - (20000 - 6000)) < 1.0
    before = e.w_bal
    e.update(200.0, 120.0)                      # 低于 CP 骑 2 分钟
    assert e.w_bal > before


def test_recovery_is_faster_when_riding_easier():
    assert recovery_tau(200.0) < recovery_tau(50.0) < recovery_tau(0.0)


def test_cp_fades_over_a_long_stage():
    e = EnergyState(cp=350.0, w_prime=22000.0, durability=1.0)
    fresh = e.effective_cp()
    e.kj_spent = 3500.0
    assert e.effective_cp() < fresh * 0.90


def test_endurance_attribute_reduces_fade():
    tough = EnergyState(cp=350.0, w_prime=22000.0, durability=1.6)
    weak = EnergyState(cp=350.0, w_prime=22000.0, durability=0.7)
    tough.kj_spent = weak.kj_spent = 3500.0
    assert tough.effective_cp() > weak.effective_cp()


def test_max_power_collapses_when_matches_are_gone():
    e = EnergyState(cp=300.0, w_prime=20000.0)
    full = e.max_power(1400.0)
    e.w_bal = 0.0
    assert e.max_power(1400.0) < full * 0.35


# --------------------------------------------------------------------------
# 属性映射层：策划改数值时的方向性保证
# --------------------------------------------------------------------------

def _attrs(**kw) -> Attributes:
    return Attributes(**kw)


def test_higher_climbing_gives_higher_climb_cp():
    lo = derive_params(_attrs(climbing=40), 65.0)
    hi = derive_params(_attrs(climbing=90), 65.0)
    assert hi.cp_climb > lo.cp_climb
    assert abs(hi.cp - lo.cp) < 1e-9          # 不应污染平路能力


def test_higher_sprint_gives_bigger_peak_and_reserve():
    lo = derive_params(_attrs(sprint=30), 72.0)
    hi = derive_params(_attrs(sprint=95), 72.0)
    assert hi.peak_anaerobic > lo.peak_anaerobic * 1.4
    assert hi.w_prime > lo.w_prime


def test_higher_time_trial_gives_lower_cda():
    lo = derive_params(_attrs(time_trial=30), 72.0)
    hi = derive_params(_attrs(time_trial=95), 72.0)
    assert hi.cda_aero < lo.cda_aero < lo.cda_hoods


def test_heavier_rider_has_larger_frontal_area():
    light = derive_params(_attrs(), 58.0)
    heavy = derive_params(_attrs(), 82.0)
    assert heavy.cda_hoods > light.cda_hoods


def test_terrain_multiplier_blends_between_flat_and_climb():
    climber = Rider("c", "爬坡手", "T", 60.0,
                    _attrs(flat=55, climbing=92), Role.CLIMBER)
    assert climber.terrain_cp_mult(0.0) == 1.0
    assert climber.terrain_cp_mult(0.03) > 1.0
    assert climber.terrain_cp_mult(0.09) > climber.terrain_cp_mult(0.03)
    # 6% 以上完全按爬坡能力结算，不再继续增长
    assert abs(climber.terrain_cp_mult(0.09) - climber.terrain_cp_mult(0.15)) < 1e-9


def test_sprinter_is_worse_uphill_than_on_the_flat():
    sprinter = Rider("s", "冲刺手", "T", 78.0,
                     _attrs(flat=80, climbing=25), Role.SPRINTER)
    assert sprinter.terrain_cp_mult(0.08) < 1.0


# --------------------------------------------------------------------------
# 集团与破风
# --------------------------------------------------------------------------

def test_draft_saves_between_25_and_45_percent():
    assert draft_factor(0, 60) == 1.0
    for rank in (1, 3, 10, 40):
        f = draft_factor(rank, 60)
        assert 0.55 <= f <= 0.75, (rank, f)


def test_crosswind_destroys_the_draft():
    calm = draft_factor(5, 60, crosswind=0.0)
    windy = draft_factor(5, 60, crosswind=7.0)
    assert windy > calm + 0.2


def test_echelon_capacity_shrinks_with_wind():
    assert echelon_capacity(0.0) > 1000
    assert echelon_capacity(4.0) > echelon_capacity(9.0) >= 6


def test_bunch_strings_out_on_climbs():
    """同样的排位，在爬坡时离队首更远——这是山上掉队的几何原因。"""
    flat = bunch_depth(40, 120, grade=0.00, crosswind=0.0)
    climb = bunch_depth(40, 120, grade=0.07, crosswind=0.0)
    assert climb > flat * 3


def test_form_groups_splits_on_a_real_gap():
    riders = build_peloton(n_teams=2, seed=1)
    race = Race(flat_stage(), riders, seed=1)
    for i, s in enumerate(race.states):
        s.distance = 0.0 if i < 8 else -500.0
        s.holding = False
    groups = form_groups(race.states)
    assert len(groups) == 2
    assert groups[0].size == 8


# --------------------------------------------------------------------------
# 比赛层：涌现行为
#
# 一场 160 人的赛段要跑十几秒，所以按 (赛道, 种子) 缓存结果，
# 多个测试共用同一场比赛。
# --------------------------------------------------------------------------

_CACHE: dict = {}


def race_once(course_fn, teams: int = 20, roster_seed: int = 7, seed: int = 42):
    key = (course_fn.__name__, teams, roster_seed, seed)
    if key not in _CACHE:
        _CACHE[key] = Race(course_fn(), build_peloton(teams, roster_seed),
                           seed=seed).run()
    return _CACHE[key]


def test_race_is_deterministic_for_a_given_seed():
    a = Race(flat_stage(), build_peloton(4, 3), seed=99).run()
    b = Race(flat_stage(), build_peloton(4, 3), seed=99).run()
    assert [s.rider.rider_id for s in a.finishers] == \
           [s.rider.rider_id for s in b.finishers]
    assert abs((a.winner.finish_time or 0) - (b.winner.finish_time or 0)) < 1e-9


def test_different_seeds_give_different_races():
    a = race_once(mountain_stage, teams=6, roster_seed=3, seed=1)
    b = race_once(mountain_stage, teams=6, roster_seed=3, seed=2)
    assert abs((a.winner.finish_time or 0) - (b.winner.finish_time or 0)) > 0.5


def test_drafting_riders_spend_less_than_the_one_on_the_front():
    result = race_once(flat_stage, teams=10, roster_seed=5, seed=7)
    pullers = [s for s in result.finishers if s.time_pulling > 1800]
    hiders = [s for s in result.finishers if s.time_pulling < 300]
    assert pullers and hiders
    avg_pull = sum(s.avg_power for s in pullers) / len(pullers)
    avg_hide = sum(s.avg_power for s in hiders) / len(hiders)
    assert avg_pull > avg_hide * 1.05, (avg_pull, avg_hide)


def test_mountain_stage_is_won_by_a_climbing_type():
    result = race_once(mountain_stage)
    top5 = {s.rider.role for _, s, _ in result.standings()[:5]}
    assert top5 & {Role.CLIMBER, Role.LEADER}


def test_flat_stage_ends_in_a_bunch_sprint():
    result = race_once(flat_stage)
    top5 = [s.rider.role for _, s, _ in result.standings()[:5]]
    assert top5.count(Role.SPRINTER) >= 3, top5
    # 大集团一起过线：前 20 名的差距应该以秒计，不是以分钟计
    assert result.gap_to_winner(result.finishers[19]) < 20.0


def _median_gap(result) -> float:
    gaps = [g for _, _, g in result.standings()]
    return gaps[len(gaps) // 2]


def test_mountain_stage_shatters_the_field_more_than_a_flat_one():
    """平路赛段大部队一起过线，山地赛段被撕成碎片。

    用中位差距而不是最后一名的差距：任何赛段都可能有一两个人被落下
    半小时，那说明不了什么；中位数才能反映整个集团有没有散架。
    """
    flat = _median_gap(race_once(flat_stage))
    mtn = _median_gap(race_once(mountain_stage))
    assert flat < 60.0, flat          # 平路：一半以上的人在冠军后一分钟内
    assert mtn > 300.0, mtn           # 山地：中位数被拉开五分钟以上
    assert mtn > flat * 10


def test_everyone_finishes_a_flat_stage():
    result = race_once(flat_stage)
    assert len(result.dnf) == 0


def test_itt_has_no_bunch_and_favours_specialists():
    result = race_once(itt_stage)
    # 计时赛不该出现"一群人同时过线"
    assert result.gap_to_winner(result.finishers[9]) > 20.0
    top = [s.rider.role for _, s, _ in result.standings()[:5]]
    assert any(r in (Role.ROULEUR, Role.LEADER) for r in top), top


def test_stage_times_are_physically_plausible():
    for fn, lo, hi in ((flat_stage, 39.0, 46.0), (mountain_stage, 28.0, 35.0),
                       (itt_stage, 43.0, 54.0)):
        result = race_once(fn)
        kmh = result.course.length_m / (result.winner.finish_time or 1) * 3.6
        assert lo <= kmh <= hi, (result.course.name, kmh)


# --------------------------------------------------------------------------
# 赛道
# --------------------------------------------------------------------------

def test_course_altitude_profile_is_consistent():
    c = mountain_stage()
    assert c.altitude_at(0) == c.start_altitude_m
    assert c.total_ascent_m > 3000
    profile = c.elevation_profile(200)
    assert len(profile) == 201
    assert profile[-1][0] <= c.length_m


def test_difficulty_ahead_sees_the_climb_coming():
    c = Course("测试", [Segment(1000, 0.0), Segment(1000, 0.10)])
    assert c.difficulty_ahead(0, 500) == 0.0
    assert c.difficulty_ahead(1200, 500) > 0.09


def _main() -> None:
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f) and pattern in n]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  ✗ {name}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _main()
