"""转会市场：报价、谈判、续约。

经理游戏的核心循环在这里，不在比赛里。比赛决定这个赛季的结果，
转会决定往后三年的结果。

**最重要的一条：玩家和 AI 走同一套评估函数。** 车手怎么看待一份报价，
只有 `evaluate_offer` 这一个实现。如果给 AI 另写一套简化逻辑，玩家迟早
会找到一个 AI 看不懂而车手却会接受的套利姿势，整个市场就崩了。这类系统
出问题几乎都出在这里。

车手看四件事，权重按现实排：

  钱      —— 最重要，但不是唯一。给到市场价的 1.3 倍以上收益递减
  出场机会 —— 一个 80 分的爬坡手不会去一支已经有两个 85 分爬坡手的队
  队伍档次 —— 世巡赛的号召力是实打实的，小队要多花钱才能抢到同一个人
  年龄     —— 30 岁以上的人更看重钱和确定性，年轻人更看重出场机会
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from game.management import market_value, squad_need
from game.world import Division, RiderProfile, Team, World

# 报价低于市场价这个比例，车手直接不谈
WALK_AWAY = 0.72

# 各等级的号召力。同样的钱，世巡赛队更容易签到人。
DIVISION_PULL = {Division.WORLD: 1.22, Division.PRO: 1.0, Division.CONTI: 0.82}


@dataclass
class Offer:
    rider_id: str
    team_id: str
    salary: int
    years: int
    from_player: bool = False


@dataclass
class Listing:
    """市场上的一个人。"""

    rider: RiderProfile
    asking: int                 # 车手的心理价位
    interest: list[str] = field(default_factory=list)   # 已经在追的队伍

    @property
    def summary(self) -> str:
        r = self.rider
        return (f"{r.name}　{r.age}岁　{r.role.value}　总评 {r.overall}"
                f"　潜力 {r.potential}　要价 {self.asking} 万")


def asking_price(rider: RiderProfile, rng: random.Random) -> int:
    """车手的心理价位。经纪人总是要得比市场价高一点。"""
    base = market_value(rider, Division.PRO)
    greed = 1.05 + 0.25 * (rider.overall / 100.0) + rng.uniform(-0.05, 0.12)
    return int(base * greed)


def open_market(world: World, rng: random.Random) -> list[Listing]:
    """当前所有自由身车手。合同年数 <= 0 的人在市场上。"""
    out = []
    for r in world.riders:
        if r.contract_years <= 0 or not r.team_id:
            out.append(Listing(r, asking_price(r, rng)))
    out.sort(key=lambda x: -x.rider.overall)
    return out


# --------------------------------------------------------------------------
# 评估
# --------------------------------------------------------------------------

def playing_time_score(rider: RiderProfile, team: Team,
                       roster: list[RiderProfile]) -> float:
    """去了能不能上场。0-1，越高越有位置。

    这一条让转会市场变成一个真正的拼图问题：不是买最贵的，
    而是买队里缺的那个。
    """
    same_role = sorted((r.overall for r in roster if r.role is rider.role),
                       reverse=True)
    need = squad_need(team, roster).get(rider.role, 0.0)
    if not same_role:
        return 1.0
    # 比这个位置上现有最好的人强多少
    edge = (rider.overall - same_role[0]) / 25.0
    return max(0.05, min(1.0, 0.45 + 0.35 * edge + 0.25 * min(1.0, need)))


def evaluate_offer(offer: Offer, rider: RiderProfile, team: Team,
                   world: World) -> float:
    """车手接受这份报价的意愿，0-1。玩家和 AI 共用这一个函数。"""
    mv = market_value(rider, team.division)
    if offer.salary < mv * WALK_AWAY:
        return 0.0

    # 钱：给到市场价 1.3 倍以上收益明显递减
    pay = math.log1p(max(0.0, offer.salary / max(1, mv) - WALK_AWAY) * 2.4)
    pay = min(1.0, pay / math.log1p((1.30 - WALK_AWAY) * 2.4))

    roster = [r for r in world.roster(team.team_id)
              if r.rider_id != rider.rider_id]
    play = playing_time_score(rider, team, roster)
    pull = DIVISION_PULL[team.division] * (0.7 + 0.3 * team.prestige / 100.0)

    # 年龄改变权重：老将要钱和确定性，年轻人要出场机会
    if rider.age >= 30:
        w_pay, w_play, w_pull = 0.62, 0.16, 0.22
    elif rider.age <= 24:
        w_pay, w_play, w_pull = 0.38, 0.38, 0.24
    else:
        w_pay, w_play, w_pull = 0.50, 0.27, 0.23

    score = w_pay * pay + w_play * play + w_pull * min(1.0, pull / 1.3)
    # 长约对年轻人是保障，对老将是负担
    if offer.years >= 3:
        score += 0.06 if rider.age <= 26 else -0.05
    return max(0.0, min(1.0, score))


def counter_offer(rider: RiderProfile, team: Team, offer: Offer,
                  world: World) -> int:
    """车手的还价：为了让他答应，至少要给到多少。"""
    lo, hi = offer.salary, max(offer.salary * 3, market_value(rider, team.division) * 2)
    for _ in range(24):
        mid = (lo + hi) / 2
        probe = Offer(offer.rider_id, offer.team_id, int(mid), offer.years)
        if evaluate_offer(probe, rider, team, world) >= 0.62:
            hi = mid
        else:
            lo = mid
    return int(hi)


# --------------------------------------------------------------------------
# 结算
# --------------------------------------------------------------------------

@dataclass
class Signing:
    rider_name: str
    team_name: str
    salary: int
    years: int
    beat: list[str] = field(default_factory=list)   # 被击败的竞争者


def resolve_offers(world: World, offers: list[Offer],
                   rng: random.Random) -> list[Signing]:
    """把一批报价结算掉。同一个车手收到多份报价时，按意愿排序取最高。

    玩家的报价没有任何特权——如果一支世巡赛队开出同样的钱，车手大概率去
    那边。想抢人就得多掏钱，或者证明自己能给他位置。
    """
    by_rider: dict[str, list[Offer]] = {}
    for o in offers:
        by_rider.setdefault(o.rider_id, []).append(o)

    signings: list[Signing] = []
    for rid, group in by_rider.items():
        rider = world.rider(rid)
        if rider.team_id:                      # 已经被签走了
            continue
        scored = []
        for o in group:
            team = world.team(o.team_id)
            scored.append((evaluate_offer(o, rider, team, world), o, team))
        scored.sort(key=lambda x: -x[0])
        best, offer, team = scored[0]
        # 高过 0.62 就是确定接受，中间地带才掷骰。
        # 全程掷骰的话会出现"刚拒绝了 20 万、转头又接受了 20 万"这种
        # 让玩家怀疑系统在乱来的画面。
        if best < 0.45:
            continue                           # 谁的条件都不够，他再等等
        if best < 0.62 and rng.random() > (best - 0.45) / 0.17:
            continue

        payroll = sum(r.salary for r in world.roster(team.team_id))
        if payroll + offer.salary > team.budget * 1.25:
            continue                           # 签不起，工资帽兜底

        rider.team_id = team.team_id
        rider.salary = offer.salary
        rider.contract_years = offer.years
        team.rider_ids.append(rid)
        signings.append(Signing(
            rider.name, team.name, offer.salary, offer.years,
            beat=[world.team(o.team_id).name.split("-")[0]
                  for _, o, _ in scored[1:4]],
        ))
    return signings


def ai_offers(world: World, listings: list[Listing], rng: random.Random,
              exclude_team: str | None = None) -> list[Offer]:
    """AI 车队对市场上的人出价。

    每支队按"位置需求 × 车手质量 ÷ 价格"挑几个目标，出价在市场价上下浮动。
    刻意让强队先挑、也出得起更高的价——现实如此，也让玩家在小队时
    必须靠眼光而不是钱包。
    """
    out: list[Offer] = []
    teams = sorted(world.teams, key=lambda t: -t.prestige)
    for team in teams:
        if team.team_id == exclude_team:
            continue
        roster = world.roster(team.team_id)
        if len(roster) >= 9:
            continue
        payroll = sum(r.salary for r in roster)
        room = team.budget * 1.15 - payroll
        if room <= 20:
            continue

        need = squad_need(team, roster)
        picks = sorted(
            listings,
            key=lambda l: -((need.get(l.rider.role, 0.1) + 0.3)
                            * l.rider.overall ** 1.7 / max(1, l.asking) ** 0.8),
        )[:6]
        for l in picks:
            mv = market_value(l.rider, team.division)
            bid = int(min(room, max(mv, l.asking) * rng.uniform(0.92, 1.16)))
            if bid < mv * WALK_AWAY:
                continue
            out.append(Offer(l.rider.rider_id, team.team_id, bid,
                             rng.randint(2, 4)))
    return out


# --------------------------------------------------------------------------
# 续约
# --------------------------------------------------------------------------

def renewal_demand(rider: RiderProfile, team: Team, world: World) -> int:
    """队内球员要求的续约年薪。"""
    mv = market_value(rider, team.division)
    # 表现好、士气高的人要得多；快退役的人要得少
    factor = 1.0 + (rider.morale - 1.0) * 0.6
    if rider.age >= 33:
        factor *= 0.82
    if rider.potential - rider.overall > 8:
        factor *= 1.10                     # 有上升空间的人知道自己值钱
    return int(mv * factor * 1.06)


def expiring(world: World, team_id: str) -> list[RiderProfile]:
    """本赛季末合同到期的队内车手。"""
    return sorted((r for r in world.roster(team_id) if r.contract_years <= 1),
                  key=lambda r: -r.overall)
