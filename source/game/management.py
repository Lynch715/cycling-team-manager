"""经营层：成长、财务、合同、转会。

一场比赛的胜负由物理决定；一支车队三年后是什么样子，由这一层决定。

四个系统互相咬合，构成经理游戏的主循环：

    赛季成绩 ──→ 赞助商满意度 ──→ 下赛季预算 ──→ 能签什么人
        ↑                                            │
        └──────── 阵容实力 ←── 训练成长 ←── 签到的人 ─┘

任何一环做成纯随机，整个循环就会失去意义。所以这里每条规则都有一个
可以说清楚的因果，玩家做完一个决定应该能预期到它三年后的后果。
"""

from __future__ import annotations

from collections import Counter

import math
import random
from dataclasses import dataclass, field

from game.season import SeasonOutcome
from game.world import Division, RiderProfile, Team, World
from sim.rider import Attributes, Role

# 属性成长的年龄曲线：26-29 是巅峰，之后开始还债
PEAK_AGE = 27
RETIRE_AGE_MIN = 30

# 各属性的成长难度：耐力和恢复练得动，冲刺爆发力基本是天赋
TRAINABILITY = {
    "flat": 1.0, "climbing": 0.95, "sprint": 0.55, "time_trial": 1.05,
    "descending": 0.8, "endurance": 1.25, "recovery": 1.1,
    "positioning": 1.15, "resilience": 0.9,
}

# 训练重点：玩家可以给每名车手指定，默认按角色自动匹配
TRAINING_FOCUS = {
    Role.LEADER: ["climbing", "time_trial", "endurance"],
    Role.CLIMBER: ["climbing", "endurance", "recovery"],
    Role.SPRINTER: ["sprint", "flat", "positioning"],
    Role.LEADOUT: ["flat", "positioning", "time_trial"],
    Role.ROULEUR: ["flat", "time_trial", "endurance"],
    Role.BREAKAWAY: ["endurance", "resilience", "recovery"],
    Role.DOMESTIQUE: ["flat", "endurance", "recovery"],
}


# --------------------------------------------------------------------------
# 成长与衰退
# --------------------------------------------------------------------------

def growth_rate(age: int) -> float:
    """年龄决定成长速度。22 岁涨得飞快，30 岁以后开始掉。"""
    if age < PEAK_AGE:
        return 1.0 + (PEAK_AGE - age) * 0.22
    if age <= 29:
        return 0.25
    return -0.35 - (age - 29) * 0.22        # 负数表示衰退


def develop(rider: RiderProfile, race_days: int, rng: random.Random,
            focus: list[str] | None = None) -> dict[str, int]:
    """一个赛季结束后的属性变化。返回 {属性: 变化量}。

    成长量取决于三件事：距离潜力天花板还有多远、年龄、这一年练了什么。
    比赛日数也算：一年只跑二十天比赛的人练不出来，跑一百二十天的人会被榨干。
    """
    focus = focus or TRAINING_FOCUS[rider.role]
    rate = growth_rate(rider.age)
    # 比赛负荷：40-80 天是最健康的区间，太少缺乏刺激，太多只剩消耗
    load = 1.0 - abs(race_days - 55) / 160.0
    changes: dict[str, int] = {}

    for key in Attributes.__dataclass_fields__:
        current = getattr(rider.attributes, key)
        if rate > 0:
            headroom = max(0.0, rider.potential - current)
            gain = (rate * TRAINABILITY[key] * headroom * 0.075
                    * (1.60 if key in focus else 0.55) * max(0.45, load))
            delta = gain + rng.gauss(0, 0.6)
        else:
            # 衰退先吃爆发力，有氧和经验掉得慢——这是真实的老将画像：
            # 冲刺越来越差，但仍然能在山里跟住，位置感甚至更好
            decay_bias = {"sprint": 1.8, "descending": 0.7,
                          "positioning": 0.25, "resilience": 0.4}.get(key, 1.0)
            delta = rate * decay_bias * 1.6 + rng.gauss(0, 0.5)
        new = max(1, min(99, round(current + delta)))
        if new != current:
            changes[key] = new - current
            setattr(rider.attributes, key, new)

    return changes


def retirement_chance(rider: RiderProfile) -> float:
    """退役概率。

    真实车队的年龄结构是金字塔：20 出头最多，32 岁以上最少。
    第一版从 34 岁才开始退役，六个赛季后 32 岁以上成了人数最多的一档，
    整个世界慢慢老死——年轻人挤不进来，玩家培养新人也就没了意义。
    所以退役从 30 岁就开始有概率，35 岁往上急剧上升；
    水平掉到跑不动的人不管多大都会提前挂靴。
    """
    base = 0.0
    if rider.age >= RETIRE_AGE_MIN:
        base = 0.06 + (rider.age - RETIRE_AGE_MIN) ** 1.9 * 0.028
    if rider.overall < 58:
        base += 0.30 if rider.age >= 27 else 0.10
    if rider.age >= 38:
        return 1.0
    return min(0.96, base)


# --------------------------------------------------------------------------
# 财务
# --------------------------------------------------------------------------

@dataclass
class TeamFinance:
    team_id: str
    sponsor_income: int
    prize_money: int
    salaries: int
    operating: int

    @property
    def income(self) -> int:
        return self.sponsor_income + self.prize_money

    @property
    def expenses(self) -> int:
        return self.salaries + self.operating

    @property
    def balance(self) -> int:
        return self.income - self.expenses


# 各等级的固定运营开销（万元）：车辆、后勤、教练、器材、差旅
OPERATING_COST = {Division.WORLD: 620, Division.PRO: 260, Division.CONTI: 95}

# 世界排名积分换算成奖金与出场费的比率（万元 / 分）
PRIZE_RATE = 0.055

# 世界水平基准：前 20 名车手的平均总评应当稳定在这个数字附近
# 这个目标值和世界实际稳定的位置差着约 +4 分：设 82 稳在 86，设 85 稳在 89。
# 因为控制器调的是青训质量，而不是直接调总评——它的平衡点在「补进来的人
# 和退役的人正好抵消」的地方，那个位置由退役曲线和成长曲线共同决定，
# 目标值只是把它往上顶。**试过设成 85 想让它稳在 85，结果稳在 89。**
# 留在 82：前十季从 82 漂到 86，之后稳在 85–87（后十季极差 2 分）。
WORLD_LEVEL_TARGET = 82.0


def settle_finance(world: World, season: SeasonOutcome) -> dict[str, TeamFinance]:
    out: dict[str, TeamFinance] = {}
    for team in world.teams:
        roster = world.roster(team.team_id)
        pts = season.team_points.get(team.team_id, 0)
        out[team.team_id] = TeamFinance(
            team_id=team.team_id,
            sponsor_income=team.budget,
            prize_money=int(pts * PRIZE_RATE),
            salaries=sum(r.salary for r in roster),
            operating=OPERATING_COST[team.division],
        )
    return out


def update_sponsors(world: World, season: SeasonOutcome,
                    rng: random.Random) -> list[str]:
    """按赛季表现调整下赛季预算。返回给玩家看的公告。

    满意度看的是"相对于花的钱，拿到了多少成绩"，不是绝对成绩。
    砸最多钱拿第三名的队会被削预算，用最少钱挤进前十的队会加预算——
    这是让中小队伍有奔头的关键，也让豪门不能躺着赢。
    """
    ranking = season.team_ranking()
    order = [tid for tid, _ in ranking]
    budgets = sorted(world.teams, key=lambda t: -t.budget)
    budget_rank = {t.team_id: i for i, t in enumerate(budgets)}

    news: list[str] = []
    for team in world.teams:
        actual = order.index(team.team_id) if team.team_id in order else len(order)
        expected = budget_rank[team.team_id]
        # 超出预期的名次数，正数代表超额完成
        surprise = expected - actual
        loyalty = max(s.loyalty for s in
                      (world.sponsor(sid) for sid in team.sponsor_ids)) or 3

        # 进前三就算交差：预算最高的队按名次差算永远只会被罚，
        # 几个赛季下来豪门被削成中游，世界失去顶端。真实的赞助商
        # 要的是"站上领奖台"，不是"必须第一"。
        if actual <= 2:
            surprise = max(surprise, 1)
        # 预算调整刻意做得比人才周期慢：车手成长要四五年，如果预算一年
        # 就能大起大落，豪门会在人才断档的那一年被永久打下去，再也回不来。
        change = 0.038 * surprise + rng.gauss(0, 0.025)
        change = max(-0.16, min(0.26, change))
        # 忠诚度高的赞助商不会因为一年不顺就砍钱
        if change < 0:
            change *= 1.0 - loyalty * 0.13

        old = team.budget
        team.budget = max(120, int(team.budget * (1 + change)))
        if abs(change) > 0.10:
            verb = "追加" if change > 0 else "削减"
            news.append(f"{team.name} 的赞助商{verb}预算 "
                        f"{abs(change):.0%}（{old} → {team.budget} 万），"
                        f"赛季排名第 {actual + 1}，预算排名第 {expected + 1}")
        team.prestige = max(5, min(99, int(team.prestige + surprise * 0.7
                                           + rng.gauss(0, 1.5))))
    return news


# --------------------------------------------------------------------------
# 合同与转会
# --------------------------------------------------------------------------

def market_value(rider: RiderProfile, division: Division) -> int:
    """一名车手在某个等级的队伍里值多少年薪。"""
    from game.generate_world import DIVISION_PREMIUM, ROLE_VALUE

    age_factor = 1.0 - abs(rider.age - PEAK_AGE) * 0.022
    youth_bonus = 1.0 + max(0, rider.potential - rider.overall) * 0.006
    base = (rider.overall ** 2.6) / 750
    return int(max(8, base * ROLE_VALUE[rider.role] * DIVISION_PREMIUM[division]
                   * age_factor * youth_bonus))


def squad_need(team: Team, roster: list[RiderProfile]) -> dict[Role, float]:
    """这支队各个位置有多缺人。数字越大越缺。"""
    from game.generate_world import SQUAD_RECIPES

    want: dict[Role, int] = {}
    for role in SQUAD_RECIPES[team.division]:
        want[role] = want.get(role, 0) + 1
    have: dict[Role, list[int]] = {}
    for r in roster:
        have.setdefault(r.role, []).append(r.overall)

    need: dict[Role, float] = {}
    for role, n in want.items():
        owned = sorted(have.get(role, []), reverse=True)[:n]
        # 缺人算满需求；人齐了但水平低，也算部分需求
        shortfall = n - len(owned)
        quality_gap = sum(max(0, 78 - o) for o in owned) / 78.0
        need[role] = shortfall + quality_gap * 0.6
    return need


@dataclass
class TransferOutcome:
    signings: list[tuple[str, str, str, int]] = field(default_factory=list)
    #                     车手  从   到   年薪
    retirements: list[str] = field(default_factory=list)
    unsigned: list[str] = field(default_factory=list)


def release_phase(world: World, rng: random.Random) -> TransferOutcome:
    """休赛期第一步：退役与合同到期。跑完之后市场上就有人了。

    刻意和"签约"分成两步：中间那个空档就是玩家的转会窗。一口气跑完的话，
    等玩家看到市场时人已经被 AI 抢光了——这正是第一版的问题。
    """
    out = TransferOutcome()

    for r in list(world.riders):
        if rng.random() < retirement_chance(r):
            out.retirements.append(r.rider_id)
            world.riders.remove(r)
            if r.team_id:
                world.team(r.team_id).rider_ids.remove(r.rider_id)

    for r in world.riders:
        if not r.team_id:
            continue
        r.contract_years -= 1
        if r.contract_years <= 0:
            world.team(r.team_id).rider_ids.remove(r.rider_id)
            r.team_id = ""
    return out


def signing_phase(world: World, rng: random.Random,
                  out: TransferOutcome,
                  player_team: str | None = None) -> TransferOutcome:
    """休赛期第二步：AI 报价、结算、无人问津的退役。"""
    from game.market import Listing, ai_offers, asking_price, resolve_offers

    for _ in range(3):                      # 三轮报价，模拟市场的来回
        free = [r for r in world.riders if not r.team_id]
        if not free:
            break
        listings = [Listing(r, asking_price(r, rng)) for r in free]
        offers = ai_offers(world, listings, rng, exclude_team=player_team)
        for s in resolve_offers(world, offers, rng):
            out.signings.append((s.rider_name, "自由身", s.team_name, s.salary))

    for r in [x for x in world.riders if not x.team_id]:
        out.unsigned.append(r.rider_id)
        world.riders.remove(r)
    return out


def run_transfer_window(world: World, rng: random.Random,
                        player_team: str | None = None) -> TransferOutcome:
    """赛季末转会窗。

    合同到期的人进入市场，各队按"位置需求 × 车手质量 ÷ 价格"出价，
    车手按"钱 + 队伍声望 + 能不能踢上主力"选择。

    刻意让市场先由强队挑：现实就是这样，小队只能在剩下的人里找。
    玩家如果想抢人，要么出更多钱，要么提前续约——这两个才是真正的决策点。
    """
    out = TransferOutcome()

    # --- 1. 退役 ---
    for r in list(world.riders):
        if rng.random() < retirement_chance(r):
            out.retirements.append(r.rider_id)
            world.riders.remove(r)
            world.team(r.team_id).rider_ids.remove(r.rider_id)

    # --- 2. 合同到期 ---
    free_agents: list[RiderProfile] = []
    for r in world.riders:
        r.contract_years -= 1
        if r.contract_years <= 0:
            free_agents.append(r)
            world.team(r.team_id).rider_ids.remove(r.rider_id)
            r.team_id = ""

    # --- 3. 市场结算 ---
    # 用 game.market 的统一评估：AI 和玩家走同一套函数。给 AI 另写一套
    # 简化逻辑的话，玩家迟早会找到一个"AI 看不懂但车手会接受"的套利姿势，
    # 整个转会市场就崩了。这类系统出问题几乎都出在这里。
    from game.market import Listing, ai_offers, asking_price, resolve_offers

    for _ in range(3):                      # 三轮报价，模拟市场的来回
        listings = [Listing(r, asking_price(r, rng))
                    for r in free_agents if not r.team_id]
        if not listings:
            break
        offers = ai_offers(world, listings, rng, exclude_team=player_team)
        for s in resolve_offers(world, offers, rng):
            out.signings.append((s.rider_name, "自由身", s.team_name, s.salary))

    free_agents = [r for r in free_agents if not r.team_id]

    # --- 4. 没人要的车手退役 ---
    for r in free_agents:
        out.unsigned.append(r.rider_id)
        world.riders.remove(r)

    return out


# --------------------------------------------------------------------------
# 升降级
# --------------------------------------------------------------------------

# 用几个赛季的滚动积分决定升降级。
#
# 单赛季定生死是最容易想到、也最糟糕的做法：一支队可能因为核心车手在
# 五月摔断锁骨就掉一级，而玩家对此无能为力。用三年滚动之后，一个坏赛季
# 会让你进入降级区、但还有两年时间补救——这才让"长期规划"这件事有意义，
# 也才有真正的降级保卫战可打。现实中的 UCI 用的正是三年滚动。
ROLLING_SEASONS = 3

# 每个等级边界每个赛季交换几支队。给 1 是刻意的：升降级要够常见到让玩家
# 每年都在意，又不能常见到让联赛结构每年洗牌。
SWAP_PER_BOUNDARY = 1

# 升降级对预算的即时冲击（乘数）。降级不只是少了拨款，赞助商也会重谈合同。
PROMOTION_BUDGET = 1.18
RELEGATION_BUDGET = 0.80

# 降级后，达到这个总评的车手有多大概率触发解约条款走人。
# 现实中顶级车手的合同几乎都有"球队失去世巡赛资格即可自由离队"这一条。
RELEASE_THRESHOLD = 76
RELEASE_CHANCE = 0.55


@dataclass
class DivisionChange:
    team_id: str
    team_name: str
    old: Division
    new: Division
    rolling_points: int
    released: list[str] = field(default_factory=list)


def rolling_points(team: Team) -> int:
    """近三个赛季的积分之和。不足三个赛季的按已有的算。"""
    return sum(team.points_history[-ROLLING_SEASONS:])


def relegation_table(world: World) -> list[tuple[Team, int, str]]:
    """给界面用的升降级形势表：每个等级内部按滚动积分排序，标出危险区。"""
    rows: list[tuple[Team, int, str]] = []
    for div in (Division.WORLD, Division.PRO, Division.CONTI):
        group = sorted((t for t in world.teams if t.division is div),
                       key=lambda t: -rolling_points(t))
        for i, t in enumerate(group):
            tag = ""
            if div is not Division.CONTI and i >= len(group) - SWAP_PER_BOUNDARY:
                tag = "降级区"
            elif div is not Division.WORLD and i < SWAP_PER_BOUNDARY:
                tag = "升级区"
            rows.append((t, rolling_points(t), tag))
    return rows


def apply_promotion_relegation(world: World, rng: random.Random
                               ) -> list[DivisionChange]:
    """按滚动积分执行升降级，并处理随之而来的连锁反应。

    降级不是只改一个标签：拨款掉一档、赞助商重谈、声望下滑，而且队里
    达到一定水平的车手会触发解约条款走人。这几条加起来才让"保住级别"
    成为一个玩家真正会紧张的目标。
    """
    changes: list[DivisionChange] = []

    for higher, lower in ((Division.WORLD, Division.PRO),
                          (Division.PRO, Division.CONTI)):
        up_pool = sorted((t for t in world.teams if t.division is lower),
                         key=lambda t: -rolling_points(t))
        down_pool = sorted((t for t in world.teams if t.division is higher),
                           key=lambda t: rolling_points(t))
        n = min(SWAP_PER_BOUNDARY, len(up_pool), len(down_pool))
        for i in range(n):
            up, down = up_pool[i], down_pool[i]
            # 只有确实超过了要降级的那支队才换。名次相当时维持现状，
            # 免得出现"升上去的比降下来的还弱"这种荒唐结果。
            if rolling_points(up) <= rolling_points(down):
                continue

            changes.append(_move(world, up, higher, rng))
            changes.append(_move(world, down, lower, rng))

    return changes


def _move(world: World, team: Team, new_div: Division,
          rng: random.Random) -> DivisionChange:
    old = team.division
    promoted = (new_div is Division.WORLD
                or (new_div is Division.PRO and old is Division.CONTI))

    ch = DivisionChange(team.team_id, team.name, old, new_div,
                        rolling_points(team))
    team.division = new_div

    from game.generate_world import BASE_GRANT
    team.budget = int(max(120, (team.budget - BASE_GRANT[old]
                                + BASE_GRANT[new_div])
                          * (PROMOTION_BUDGET if promoted
                             else RELEGATION_BUDGET)))
    team.prestige = max(5, min(99, team.prestige + (7 if promoted else -8)))

    if not promoted:
        # 解约条款：降级之后留不住好车手，这是降级最痛的部分
        for r in world.roster(team.team_id):
            if r.overall >= RELEASE_THRESHOLD and rng.random() < RELEASE_CHANCE:
                r.contract_years = 0        # 转会窗里会自动变成自由身
                ch.released.append(r.name)
    return ch


# --------------------------------------------------------------------------
# 角色重排
# --------------------------------------------------------------------------

# 每个角色看重哪些属性（权重和为 1）
_ROLE_STATS: dict = {}

ROLE_PROFILE = {
    Role.LEADER: {"climbing": .35, "time_trial": .30, "endurance": .20,
                  "resilience": .15},
    Role.CLIMBER: {"climbing": .60, "endurance": .25, "recovery": .15},
    Role.SPRINTER: {"sprint": .60, "flat": .20, "positioning": .20},
    Role.LEADOUT: {"flat": .40, "positioning": .40, "sprint": .20},
    Role.ROULEUR: {"flat": .45, "time_trial": .35, "endurance": .20},
    Role.BREAKAWAY: {"endurance": .40, "resilience": .30, "flat": .30},
    Role.DOMESTIQUE: {"flat": .40, "endurance": .40, "recovery": .20},
}


def _role_stats(world: World) -> dict:
    """每个角色画像在全世界的均值与标准差，用来把契合度换成 z 分。"""
    out = {}
    for role, weights in ROLE_PROFILE.items():
        vals = [sum(getattr(r.attributes, k) * w for k, w in weights.items())
                for r in world.riders]
        n = max(1, len(vals))
        mu = sum(vals) / n
        var = sum((v - mu) ** 2 for v in vals) / n
        out[role] = (mu, math.sqrt(var) or 1.0)
    return out


def reassign_roles(world: World) -> list[tuple[str, Role, Role]]:
    """按当前属性重排每支队的角色分工。返回 [(车手, 旧角色, 新角色)]。

    车手会成长成和入队时不一样的人。一个练出爬坡的工兵实际上就是总成绩
    核心，如果阵容表还写着"工兵"，玩家会看到"我的工兵在赢大环赛"这种
    莫名其妙的画面，也不会想到该把队伍围绕他重建。

    分配用贪心匹配：把 (车手, 角色) 的契合度全部算出来，从最高的开始占坑。
    每个角色的坑位数由该等级的阵容配方决定。
    """
    from game.generate_world import SQUAD_RECIPES

    global _ROLE_STATS
    _ROLE_STATS = _role_stats(world)
    changes: list[tuple[str, Role, Role]] = []
    for team in world.teams:
        roster = world.roster(team.team_id)
        if not roster:
            continue
        slots: list[Role] = list(SQUAD_RECIPES[team.division])
        while len(slots) < len(roster):
            slots.append(Role.DOMESTIQUE)

        remaining: dict[Role, int] = {}
        for s in slots:
            remaining[s] = remaining.get(s, 0) + 1

        # **契合度要先标准化再比。**
        #
        # 原来是全局贪心：算出每个人对每个角色的契合度，从最高的开始占坑。
        # 问题在于不同角色的画像「峰度」不一样——CLIMBER 是 0.60×爬坡，
        # 权重集中；LEADER 是 0.35×爬坡+0.30×计时+0.20×耐力+0.15×韧性，
        # 摊得很平。**一个顶级爬坡型车手在 CLIMBER 上的原始分永远高于他在
        # LEADER 上的原始分**，于是每支队最好的那个人被贴上「爬坡手」，
        # 队长的位置留给第二好的。
        #
        # 这个 bug 只在换季之后出现：初始世界是照配方生成的，第一个赛季
        # 三个大环赛冠军全是总成绩核心；跑完一次休赛期重排，从第二个赛季
        # 起全变成爬坡手。**世界不是慢慢漂的，是一夜之间被重新贴了标签。**
        # 我为此调了三轮属性、赛季目标和阵容配方，全都没打中。
        #
        # 试过「按角色重要性依次填坑」（队长先选人），结果矫枉过正：
        # 每支队最强的两个人一律变成队长和冲刺手，全能型的 per100 从 104
        # 掉到 25。正确的做法是把分数换成 z 分——比的不是原始契合度，
        # 是「这个人在这个角色上有多出众」，峰度的差异就此抵消。
        pairs = []
        for r in roster:
            for role, weights in ROLE_PROFILE.items():
                fit = sum(getattr(r.attributes, k) * w for k, w in weights.items())
                mu, sd = _ROLE_STATS[role]
                pairs.append(((fit - mu) / sd, r.rider_id, role))
        pairs.sort(key=lambda x: -x[0])

        remaining: dict[Role, int] = {}
        for s in slots:
            remaining[s] = remaining.get(s, 0) + 1

        taken: set[str] = set()
        for _, rid, role in pairs:
            if rid in taken or remaining.get(role, 0) <= 0:
                continue
            rider = world.rider(rid)
            if rider.role is not role:
                changes.append((rider.name, rider.role, role))
                rider.role = role
            taken.add(rid)
            remaining[role] -= 1
        # 没匹配上的一律当工兵
        for r in roster:
            if r.rider_id not in taken and r.role is not Role.DOMESTIQUE:
                changes.append((r.name, r.role, Role.DOMESTIQUE))
                r.role = Role.DOMESTIQUE
    return changes


# --------------------------------------------------------------------------
# 赛季推进
# --------------------------------------------------------------------------

def finish_offseason(world: World, rng: random.Random,
                     transfers: TransferOutcome,
                     player_team: str | None = None) -> dict:
    """玩家转会窗关闭之后，把休赛期剩下的事跑完。"""
    from game.generate_world import assign_season_targets

    signing_phase(world, rng, transfers, player_team)
    _fill_squads(world, rng)
    role_changes = reassign_roles(world)
    for r in world.riders:
        r.salary = market_value(r, world.team(r.team_id).division)
    assign_season_targets(world, rng)
    return {"transfers": transfers, "role_changes": role_changes}


def advance_season(world: World, season: SeasonOutcome,
                   rng: random.Random, player_team: str | None = None,
                   pause_for_transfers: bool = False) -> dict:
    """把世界推进到下一个赛季。返回一份给玩家看的赛季总结。

    pause_for_transfers=True 时只跑到"合同到期、市场开放"为止，
    把签约留给玩家；玩家操作完再调 finish_offseason()。
    """
    from game.generate_world import assign_season_targets

    race_days: dict[str, int] = {}
    for out in season.events:
        for st in out.stages:
            for rid in st.order:
                race_days[rid] = race_days.get(rid, 0) + 1

    finance = settle_finance(world, season)
    news = update_sponsors(world, season, rng)

    # 成长：先算变化再涨年龄，避免"22 岁按 23 岁的曲线成长"
    growth_report: list[tuple[str, int]] = []
    for r in world.riders:
        before = r.overall
        # 玩家队伍的成长由 game.training 单独结算（含专项、强度、集训营），
        # 在这里跳过，避免同一个赛季被练两次
        if player_team and r.team_id == player_team:
            r.age += 1
            r.fatigue = 0.0
            r.form = round(min(1.10, max(0.90, rng.gauss(1.0, 0.035))), 3)
            r.morale = round(min(1.12, max(0.88, r.morale * 0.7 + 0.3)), 3)
            growth_report.append((r.rider_id, 0))
            continue
        develop(r, race_days.get(r.rider_id, 15), rng)
        r.age += 1
        r.fatigue = 0.0
        r.form = round(min(1.10, max(0.90, rng.gauss(1.0, 0.035))), 3)
        r.morale = round(min(1.12, max(0.88, r.morale * 0.7 + 0.3)), 3)
        growth_report.append((r.rider_id, r.overall - before))

    # 记下本赛季积分，然后按三年滚动决定升降级。
    # 顺序很关键：必须在转会窗之前——车手要根据"明年这支队在哪个级别"
    # 来决定去留和身价，降级队的球星才会真的跑掉。
    for tm in world.teams:
        tm.points_history.append(season.team_points.get(tm.team_id, 0))
        tm.points_history = tm.points_history[-ROLLING_SEASONS:]
    division_changes = apply_promotion_relegation(world, rng)

    transfers = release_phase(world, rng)
    if not pause_for_transfers:
        transfers = signing_phase(world, rng, transfers, player_team)
        _fill_squads(world, rng)

    # 按新的属性重排分工：练出来的人该上位，练废的人该让位
    role_changes = reassign_roles(world) if not pause_for_transfers else []

    world.season += 1
    if not pause_for_transfers:
        for r in world.riders:
            r.salary = market_value(r, world.team(r.team_id).division)
        assign_season_targets(world, rng)

    return {
        "race_days": race_days,
        "finance": finance,
        "news": news,
        "transfers": transfers,
        "division_changes": division_changes,
        "growth": growth_report,
        "role_changes": role_changes,
    }


def _fill_squads(world: World, rng: random.Random) -> None:
    """从青训体系补人，保证每队至少八人。

    新人属性明显低于一线，但潜力高。这是小队唯一的翻身机会，
    也是"我要不要卖掉当家球星换未来"这个决定成立的前提。
    """
    from game.generate_world import SQUAD_RECIPES
    from game.names import make_name, pick_nation
    from game.world import RiderProfile
    from sim.roster import make_rider

    counter = max((int(r.rider_id[1:]) for r in world.riders), default=0)

    # 青训水平跟着当前世界水平走，形成一个负反馈：世界变弱就多出好苗子，
    # 世界过强就收紧。没有这条闭环，退役和成长的收支差会让世界逐年下滑，
    # 玩到第十个赛季会发现"当年的巨星放到今天无人可挡"——这是所有长线
    # 经理游戏最容易翻车的地方。
    elite = sorted((r.overall for r in world.riders), reverse=True)[:20]
    level = sum(elite) / max(1, len(elite))

    # 纯比例控制会震荡，因为这个回路有四五个赛季的滞后：今年招的新人
    # 要五年后才进得了前二十。等到「世界变弱了」才加码，加的那一批到位时
    # 世界已经自己回来了，于是冲过头，再往下压，来回摆动 4–5 分。
    #
    # 补一个前馈项：**看的不是今年的世界水平，是几年后的。** 二十四岁以下
    # 那批人的潜力就是未来的前二十——一代好苗子已经在路上时，现在就该收手。
    # 这不需要记住上一年的状态，所以存档格式不用动。
    young = sorted((r.potential for r in world.riders if r.age <= 24),
                   reverse=True)[:20]
    pipeline = sum(young) / max(1, len(young)) if young else level
    predicted = 0.62 * level + 0.38 * pipeline
    # 上下限原来是 (−0.20, +0.45)——**不对称的**。控制器能大幅加强青训，
    # 却几乎不能削弱，于是世界只会往上冲过头，冲上去之后再也压不回来。
    # 头七个赛季从 82 涨到 87 就是这么来的，那不是震荡，是单向漂移。
    # 增益从 1/22 降到 1/40，上下限收紧到 ±0.26。
    # 这个回路有四五个赛季的死区时间——今年招的人五年后才进前二十——
    # **对死区时间大的系统，正确的做法是把增益调小，而不是加微分项。**
    # 增益够大就一定会以死区时间为周期来回摆；调小之后回得慢，但不摆。
    correction = max(-0.26, min(0.26,
                                (WORLD_LEVEL_TARGET - predicted) / 40.0))

    for team in world.teams:
        roster = world.roster(team.team_id)
        recipe = SQUAD_RECIPES[team.division]
        while len(roster) < 8:
            counter += 1
            # **按「缺什么」补，不是按「现在有几个人」补。**
            #
            # 原来这里是 `recipe[len(roster) % len(recipe)]`——用花名册长度
            # 去索引配方。一支队有 7 个人、丢的是总成绩核心，它补进来的是
            # `recipe[7]`，也就是工兵；总成绩核心永远补不回来。
            #
            # 后果在第一个赛季完全看不出来（初始世界是照配方生成的），
            # 从第二个赛季开始角色结构就开始漂：跑 20 个赛季，大环赛总成绩
            # 冠军在 2026 年是三个总成绩核心，2027 年之后全是爬坡手——
            # 不是因为爬坡手更强，是因为**世界上快没有总成绩核心了**。
            # 我为此调了三轮属性和赛季目标，全都没打中。
            have = Counter(r.role for r in roster)
            want = Counter(recipe)
            missing = [ro for ro in recipe if have[ro] < want[ro]]
            role = missing[0] if missing else recipe[len(roster) % len(recipe)]
            # 青训水平必须撑得住换血：退役的是 80 分的老将，补进来的
            # 如果只有 60 分，几个赛季后整个世界的水平会一路下滑，
            # 玩家会发现"当年的巨星放到今天是无敌的"。
            tier = {Division.WORLD: 0.80, Division.PRO: 0.55,
                    Division.CONTI: 0.35}[team.division] + correction
            base = make_rider(f"R{counter:03d}", "", team.team_id, role, tier, rng)
            nation = pick_nation(rng)
            age = rng.choice([20, 21, 21, 22, 22, 23])
            profile = RiderProfile(
                rider_id=base.rider_id, name=make_name(nation, rng),
                nation=nation, age=age, body_mass_kg=base.body_mass_kg,
                height_cm=int(168 + (base.body_mass_kg - 65) * 0.9),
                team_id=team.team_id, role=role, attributes=base.attributes,
                potential=0,
                art_portrait=f"portrait-{counter % 32 + 1:02d}",
                art_body=("body-lean" if base.body_mass_kg < 64 else
                          "body-strong" if base.body_mass_kg > 74 else
                          "body-standard"),
                contract_years=rng.randint(3, 5),
            )
            profile.potential = int(min(99, profile.overall
                                        + rng.randint(10, 28)))
            profile.salary = market_value(profile, team.division)
            world.riders.append(profile)
            team.rider_ids.append(profile.rider_id)
            roster.append(profile)


def math_guard() -> None:  # pragma: no cover - 保留 math 引用，便于后续扩展
    _ = math.e
