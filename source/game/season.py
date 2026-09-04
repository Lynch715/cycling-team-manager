"""赛季推进：跑一场赛事、跑一整个赛季。

两种结算方式共存：
  · engine="quick" 用快速结算，一整季 107 个比赛日几秒钟跑完
  · engine="full"  用完整引擎逐秒模拟，玩家要亲自看的比赛用这个

两者由 calibrate_quick.py 对账保证描述同一个世界。
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field

from game.classification import (
    Classification, StageResult, award_climb_points, world_ranking_points,
)
from game.courses import build_course
from game.quickresolve import RACE_SIGMA, resolve
from game.world import Division, RaceEvent, RaceTier, World
from sim.rider import Role
from sim.course import Course
from sim.race import Race

# 各等级赛事的参赛队伍构成与出场人数
ENTRY_RULES = {
    RaceTier.GRAND_TOUR:  dict(world=6, pro=7, conti=5, squad=8),
    RaceTier.MONUMENT:    dict(world=6, pro=7, conti=5, squad=7),
    RaceTier.WORLD_TOUR:  dict(world=6, pro=6, conti=4, squad=7),
    RaceTier.PRO_SERIES:  dict(world=2, pro=7, conti=7, squad=7),
    RaceTier.NATIONAL:    dict(world=0, pro=4, conti=7, squad=6),
}

# 选人时各类赛段的权重。关键在于平路赛段的权重极低：
# 一个大环赛里一半是平路赛段，但它们几乎不产生时间差距，对总成绩毫无意义。
# 按赛段数量算权重会得出"大环赛主要是平路赛"这个荒谬结论，
# 于是队伍会派一堆平路手去打三周高山赛——这正是第一版跑出来的结果。
STAGE_WEIGHT = {
    "summit_finish": 3.0, "mountain": 3.0, "itt": 1.2,
    # 团体计时赛的权重比个人计时赛低一点：它拉开的时间差实际不大
    # （强弱队之间通常一两分钟），但它对**选谁参赛**的影响很大——
    # 一支队必须带够能撑到计时位的人。
    "ttt": 0.9,
    "hilly": 1.0, "cobbled": 0.8, "flat": 0.35,
}

# 角色与赛事地形的匹配度。参数是爬坡权重 0-1。
ROLE_FIT = {
    Role.LEADER:     lambda b: 1.0 + 0.45 * b,
    Role.CLIMBER:    lambda b: 0.8 + 0.70 * b,
    Role.SPRINTER:   lambda b: 1.25 - 0.75 * b,
    Role.LEADOUT:    lambda b: 1.15 - 0.70 * b,
    Role.ROULEUR:    lambda b: 1.10 - 0.35 * b,
    Role.BREAKAWAY:  lambda b: 1.0,
    Role.DOMESTIQUE: lambda b: 0.95,
}


def terrain_bias(event: RaceEvent) -> float:
    """这场赛事有多"看爬坡"，0-1。按产生时间差距的能力加权，不按赛段数量。"""
    total = sum(STAGE_WEIGHT[s.stage_type] for s in event.stages)
    climb = sum(STAGE_WEIGHT[s.stage_type] for s in event.stages
                if s.stage_type in ("mountain", "summit_finish"))
    return climb / max(1e-6, total)


# 每个比赛日累积的疲劳，按赛段难度缩放
# 每个比赛日累积的疲劳。0.075 时一个 21 天的大环赛要加 1.8——顶到上限
# 还溢出一倍，疲劳这条轴在大环赛之后就没有分辨率了：跑完的人和跑崩的人
# 都是 1.0。压到 0.042 之后一个大环赛正好把人推到接近上限而不溢出，
# 这也是现实：三周之后确实是被榨干了，但榨干的程度彼此不同。
FATIGUE_PER_STAGE = 0.042
FATIGUE_RECOVERY_PER_REST_DAY = 0.020


@dataclass
class EventOutcome:
    event: RaceEvent
    classification: Classification
    stages: list[StageResult]
    jerseys: dict[str, str]
    ranking_points: dict[str, int] = field(default_factory=dict)

    @property
    def winner(self) -> str | None:
        gc = self.classification.gc_standings()
        return gc[0][0] if gc else None


# --------------------------------------------------------------------------
# 参赛名单
# --------------------------------------------------------------------------

def rest(world: World, days: int) -> None:
    """两场赛事之间的空档，所有人按恢复属性回血。

    **这个函数原来只有批量赛季那条路径在调，生涯模式从来没调过。**
    后果是：跑到第 12 场比赛，全世界所有人的疲劳都顶到 1.0，然后一直
    顶到赛季结束。因为是所有人一起顶满，相对强弱没变，成绩表看上去
    完全正常——所以它藏了很久。

    但它悄悄抹掉了一整个玩法维度：轮换阵容、让人休息、恢复属性值不值钱，
    在疲劳封顶的世界里全都没有意义。
    """
    if days <= 0:
        return
    for p in world.riders:
        p.fatigue = max(0.0, p.fatigue - days * FATIGUE_RECOVERY_PER_REST_DAY
                        * (0.6 + p.attributes.recovery / 130.0))


def pick_entrants(world: World, event: RaceEvent,
                  rng: random.Random,
                  lineup: tuple[str, list[str]] | None = None
                  ) -> dict[str, list[str]]:
    """决定哪些队参赛、各派谁上场。返回 {车队 id: [车手 id]}。

    选人不是随便挑八个：山地赛事优先带爬坡手，平路赛事优先带冲刺列车。
    这一条让"我该囤什么样的车手"变成一个有后果的决定。
    """
    rules = ENTRY_RULES[event.tier]
    by_div = {d: [t for t in world.teams if t.division is d] for d in Division}
    for d in by_div:
        by_div[d].sort(key=lambda t: -t.prestige)

    teams = (by_div[Division.WORLD][:rules["world"]]
             + by_div[Division.PRO][:rules["pro"]]
             + by_div[Division.CONTI][:rules["conti"]])

    climb_bias = terrain_bias(event)

    entries: dict[str, list[str]] = {}
    for team in teams:
        roster = world.roster(team.team_id)

        def fitness(r) -> float:
            a = r.attributes
            score = (a.climbing * climb_bias + a.flat * (1 - climb_bias)
                     + a.endurance * 0.5 + a.sprint * 0.3 * (1 - climb_bias))
            score *= ROLE_FIT[r.role](climb_bias)
            score *= r.form * (1.0 - 0.6 * r.fatigue)
            # 赛季目标：明星车手一年只主攻两三场，其余时间根本不出现。
            # 这是现实中"同一个人不可能赢下三个大环赛"的真正原因——
            # 不是他不够强，是他不去。少了这一条，赛季会退化成
            # "最强的人拿走所有奖杯"，整个转会和规划玩法都失去意义。
            if r.target_races:
                score *= 1.9 if event.race_id in r.target_races else 0.35
            return score + rng.random() * 4

        picked = sorted(roster, key=lambda r: -fitness(r))[:rules["squad"]]
        entries[team.team_id] = [r.rider_id for r in picked]

    # 玩家指定的名单覆盖自动挑选。只在他这支队确实参赛时才生效——
    # 一支洲际队报不了名的大环赛，玩家再怎么排名单也上不了场。
    if lineup:
        tid, ids = lineup
        if tid in entries and ids:
            valid = [r.rider_id for r in world.roster(tid)]
            keep = [i for i in ids if i in valid][:rules["squad"]]
            if keep:
                entries[tid] = keep
    return entries


# --------------------------------------------------------------------------
# 单个赛段
# --------------------------------------------------------------------------

def _kom_and_sprint(course: Course, order: list[str],
                    rng: random.Random,
                    kom_order: dict | None = None) -> tuple[dict, dict]:
    """发放爬坡与冲刺积分。

    **通过顺序不等于终点名次。** 突围的人先过点、拿走积分，然后被追回，
    终点可能排在五十名开外——爬坡王衫的整个玩法就建立在这个差别上。

    完整引擎现在会记录真实的越点顺序（`RaceResult.kom_order`），有就用真的。
    快速结算给不出这个数据——它根本没有「比赛过程」这个概念——只能继续用
    「终点名次加扰动」近似。**两条路径在这一项上是不一致的，这是有意接受的**：
    途中积分不影响总成绩，而为了它去给快速结算编一套假的过程，
    代价远大于收益。玩家亲自看的那场比赛拿到的是真数据。
    """
    kom: dict[str, int] = defaultdict(int)
    sprint: dict[str, int] = defaultdict(int)
    if not order:
        return kom, sprint
    pool = order[:40]
    for i, point in enumerate(course.koms):
        real = (kom_order or {}).get(i)
        if real:
            shuffled = real[:40]
        else:
            shuffled = sorted(pool, key=lambda r: pool.index(r) + rng.random() * 12)
        award = award_climb_points(shuffled, point.category, point.points)
        target = sprint if point.category == "sprint" else kom
        for rid, pts in award.items():
            target[rid] += pts
    return kom, sprint


# 各地形的降雨概率。比利时的春天和西班牙的夏天不是一回事，
# 这个数字直接决定了石板路赛段该不该赌轮胎。
RAIN_CHANCE = {
    "cobbles": 0.42, "dutch": 0.35, "coast": 0.28, "alps": 0.30,
    "pyrenees": 0.26, "italian_hills": 0.20, "city": 0.22, "desert": 0.04,
}


def draw_weather(terrain: str, rng: random.Random) -> float:
    """抽一次天气，返回 0（干燥）到 1（大雨）。"""
    if rng.random() > RAIN_CHANCE.get(terrain, 0.25):
        return 0.0
    return round(rng.uniform(0.35, 1.0), 2)


def run_stage(world: World, event: RaceEvent, stage_index: int,
              entries: dict[str, list[str]], rng: random.Random,
              engine: str = "quick",
              race_form: dict[str, float] | None = None,
              rain: float = 0.0,
              directives: dict | None = None,
              watch: dict | None = None) -> StageResult:
    spec = event.stages[stage_index]
    course = build_course(spec, name_prefix=f"{event.name} ",
                          course_id=f"{event.race_id}-{stage_index + 1}")

    kom_order = None          # 完整引擎跑出来的真实越点顺序，快速结算没有
    rider_ids = [rid for ids in entries.values() for rid in ids]
    profiles = [world.rider(rid) for rid in rider_ids]
    sim_riders = [p.to_sim_rider() for p in profiles]

    if spec.stage_type == "ttt":
        # 团体计时赛不走集团模型：每支队单独出发，队内轮转，取第 N 人计时。
        # 全队拿同一个成绩——这是规则，也是它在总成绩上的杀伤力所在：
        # 一支厚度不够的队会把自己的队长一起拖下水。
        from sim.ttt import team_time_trial
        by_team: dict[str, list] = {}
        for p_ in profiles:
            by_team.setdefault(p_.team_id, []).append(p_)
        nth = 4 if len(next(iter(by_team.values()), [])) >= 6 else 3
        times = {}
        for tid, squad in by_team.items():
            res = team_time_trial([p_.to_sim_rider() for p_ in squad],
                                  course, count_nth=nth)
            for p_ in squad:
                times[p_.rider_id] = res.time_s
        order = sorted(times, key=lambda rid: (times[rid], rid))
        dnf = []
    elif watch is not None:
        # 玩家要亲自看这一场：完整引擎跑，同时录像。
        # 录下来的名次和用时直接就是这一段的结果——他看到的必须算数。
        from game.watch import record
        order, times, dnf, replay = record(
            course, sim_riders, spec.seed, rain,
            set(watch.get("mine") or ()),
            {p.rider_id: p.name for p in profiles},
            terrain=spec.terrain,
            colors={t.team_id: t.color_primary for t in world.teams})
        watch["replay"] = replay
    elif engine == "full":
        result = Race(course, sim_riders, dt=1.0, seed=spec.seed,
                      rain=rain, directives=directives).run()
        order = [s.rider.rider_id for s in result.finishers]
        times = {s.rider.rider_id: (s.finish_time or 0.0)
                 for s in result.finishers}
        dnf = [s.rider.rider_id for s in result.dnf]
        kom_order = result.kom_order
    else:
        quick = resolve(sim_riders, course, spec.stage_type,
                        random.Random(spec.seed), form=race_form, rain=rain,
                        directives=directives)
        order, times, dnf = quick.order, quick.times, quick.dnf

    kom, sprint = _kom_and_sprint(course, order, rng, kom_order)
    return StageResult(stage_index=stage_index, stage_type=spec.stage_type,
                       order=order, times=times, dnf=dnf,
                       kom_points=dict(kom), sprint_points=dict(sprint))


# --------------------------------------------------------------------------
# 单场赛事
# --------------------------------------------------------------------------

def squad_size(event: RaceEvent) -> int:
    """这场比赛每队能派几个人。名单界面要用。"""
    return ENTRY_RULES[event.tier]["squad"]


def run_event(world: World, event: RaceEvent, rng: random.Random,
              engine: str = "quick", recorder=None,
              orders: dict | None = None,
              watch: dict | None = None,
              lineup: tuple[str, list[str]] | None = None) -> EventOutcome:
    """recorder 是一个可选回调：每跑完一个赛段就把成绩交出去。

    比赛引擎自己不该知道「数据库」这种东西——传一个回调进来，
    要不要落库、落到哪儿，由调用方决定。
    """
    entries = pick_entrants(world, event, rng, lineup)
    entered = {rid for ids in entries.values() for rid in ids}

    # 每个人都带着战术上路，不只是玩家的队。没给指令的按角色取默认值——
    # AI 车队因此也会派工兵领骑、让冲刺手躲到最后，而不是一群没有分工的人
    # 各跑各的。这一层之前整个是空的：career 算好 orders 就扔了。
    from game.orders import build_directives
    directives = build_directives([world.rider(rid) for rid in entered],
                                  orders)

    classification = Classification()
    stages: list[StageResult] = []

    # 赛事级状态：每名车手带着一个"这场比赛的状态"来，整场不变。
    # 有人赛前感冒，有人正好巅峰——这决定了多日赛的主线，
    # 而不是每天重掷一次运气。
    hardest = max(event.stages, key=lambda s: STAGE_WEIGHT[s.stage_type])
    sigma = RACE_SIGMA[hardest.stage_type]
    race_form = {rid: math.exp(rng.gauss(0.0, sigma)) for rid in entered}

    for i in range(len(event.stages)):
        rain = draw_weather(event.stages[i].terrain, rng)
        w = watch if (watch is not None
                      and watch.get("stage_index", 0) == i) else None
        result = run_stage(world, event, i, entries, rng, engine,
                           race_form, rain, directives, watch=w)
        classification.apply(result)
        stages.append(result)
        if recorder is not None:
            recorder(event, i, result)
        # 每个比赛日都在消耗车手，多日赛尤其明显
        hard = result.stage_type in ("mountain", "summit_finish", "cobbled")
        for rid in entered:
            p = world.rider(rid)
            p.fatigue = min(1.0, p.fatigue
                            + FATIGUE_PER_STAGE * (1.5 if hard else 1.0)
                            # 除数原来是 260：恢复 50 和恢复 75 的人每天累积
                            # 的疲劳只差 8%，跑完一个大环赛疲劳只拉开 0.07，
                            # 折算成状态是 1.9%。**「三周不崩」这件事在数值上
                            # 几乎不存在**，于是总成绩核心相对纯爬坡手的全部
                            # 优势——耐力、恢复、韧性——都收不到回报。
                            # 收到 150 之后同样两个人相差 23%，二十一天累起来
                            # 才是一个能决定大环赛的差距。
                            * (1.0 - p.attributes.recovery / 150.0))

    ages = {r.rider_id: r.age for r in world.riders}
    jerseys = classification.jerseys(ages)

    points: dict[str, int] = {}
    gc = classification.gc_standings()
    for place, (rid, _) in enumerate(gc[:40], start=1):
        points[rid] = world_ranking_points(place, event.tier.value,
                                           event.prestige, is_gc=True)
    # 赛段冠军另计积分，这样冲刺手在大环赛里也有明确的目标
    if event.is_stage_race:
        for st in stages:
            if st.winner:
                points[st.winner] = points.get(st.winner, 0) + world_ranking_points(
                    1, event.tier.value, event.prestige, is_gc=False)

    if gc:
        world.rider(gc[0][0]).career_wins += 1
        world.rider(gc[0][0]).morale = min(1.15, world.rider(gc[0][0]).morale + 0.05)

    return EventOutcome(event, classification, stages, jerseys, points)


# --------------------------------------------------------------------------
# 整个赛季
# --------------------------------------------------------------------------

@dataclass
class SeasonOutcome:
    events: list[EventOutcome]
    rider_points: dict[str, int]
    team_points: dict[str, int]

    def rider_ranking(self) -> list[tuple[str, int]]:
        return sorted(self.rider_points.items(), key=lambda x: -x[1])

    def team_ranking(self) -> list[tuple[str, int]]:
        return sorted(self.team_points.items(), key=lambda x: -x[1])


def run_season(world: World, seed: int = 1, engine: str = "quick",
               progress=None) -> SeasonOutcome:
    rng = random.Random(seed)
    rider_points: dict[str, int] = defaultdict(int)
    team_points: dict[str, int] = defaultdict(int)
    outcomes: list[EventOutcome] = []

    day = 0
    for event in sorted(world.calendar, key=lambda e: e.start_day):
        # 两场赛事之间的空档用来恢复
        rest(world, max(0, event.start_day - day))
        day = event.start_day + event.days

        outcome = run_event(world, event, rng, engine)
        outcomes.append(outcome)
        for rid, pts in outcome.ranking_points.items():
            rider_points[rid] += pts
            team_points[world.rider(rid).team_id] += pts
        if progress:
            progress(event, outcome)

    return SeasonOutcome(outcomes, dict(rider_points), dict(team_points))
