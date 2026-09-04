"""快速结算：不逐秒模拟也能得到和完整引擎一致的比赛结果。

**为什么必须有这个东西**：一个赛季 107 个比赛日，用完整引擎逐秒跑要 25
分钟以上。玩家点"推进到下一场"时不可能等这么久。但如果快速结算和完整
引擎给出的是两套不同的世界规律，玩家迟早会发现"我亲自看的比赛"和
"系统替我跑的比赛"里强弱关系不一样——那时整个游戏的可信度就没了。

**所以做法是**：快速结算不是另写一套规则，而是把完整引擎的物理结论
压缩成闭式公式。三条结论直接来自引擎：

  1. 爬坡时间几乎完全由每公斤功率决定，两人差 5% 的 W/kg，
     爬坡段就差 5% 的时间
  2. 平路上跟车能省 40%，所以只要能力差距不超过某个阈值，
     所有人都会一起过线——差距为零，名次由冲刺决定
  3. 一旦被甩出集团就要独自顶风，速度立刻掉一档，
     所以掉队的代价是断崖式的，不是线性的

`calibrate_quick.py` 会把两者跑在同一条赛道上对账，超出容差就报警。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from sim.course import Course
from sim.rider import Rider, Role

# 差距形状参数，由 calibrate_quick.py 对着完整引擎在同一条赛道上拟合得到。
#
#   落后时间 / 冠军成绩 = alpha × rel + beta × max(0, rel - drop) ^ gamma
#
# rel 是"能力落后冠军的比例"。两项各自对应一个物理现实：
#   alpha 项——爬坡时间与每公斤功率成反比，弱 5% 就慢 5%，是连续的
#   beta 项——一旦被甩出集团就要独自顶风，代价是断崖式的，所以指数很小
# 平路赛段 alpha = 0：只要跟得住，能力差多少都是同一个时间过线。
GAP_SHAPE = {
    # 平路这一组我试过重新拟合，失败了，把过程记在这里，免得下一个人
    # 再走一遍：
    #
    # 症状是快速结算的中位差距只有 0 秒，完整引擎是 285 秒——全场同一个
    # 成绩。对着引擎的分位数解出 drop=0.046 / beta=1.50 / gamma=1.90，
    # 中位和 80 分位都对上了，但前十的角色重叠从 60% 掉到 30%：冲刺手
    # 全被甩掉了，平路赛段变成全能型的天下。
    #
    # 原因是 `_flat_merit` 把体重和迎风面积按「独自顶风」来算，而集团里
    # 的人根本不顶风——真实的破风省掉三成半的功率，重的冲刺手因此能跟住。
    # 现在这个宽松的 drop（0.228）实际上是在补偿 `_flat_merit` 的这个偏差，
    # **两个错的数正好抵消。** 要真修，得先让 _flat_merit 按集团里的
    # 有效迎风面积算（cda 的指数从 1 降到 0.5 左右），再重新拟合这一组。
    # 那是一次独立的改动，不该和别的事混在一起做。
    "flat":          dict(alpha=0.000, drop=0.228, beta=0.276, gamma=0.234),
    "cobbled":       dict(alpha=0.000, drop=0.120, beta=0.657, gamma=1.299),
    "hilly":         dict(alpha=0.000, drop=0.155, beta=0.503, gamma=0.927),
    "mountain":      dict(alpha=0.090, drop=0.012, beta=5.400, gamma=2.500),
    "summit_finish": dict(alpha=0.095, drop=0.014, beta=5.600, gamma=2.500),
    "itt":           dict(alpha=0.000, drop=9.990, beta=0.000, gamma=1.000),
    # 团体计时赛由 sim.ttt 单独结算，这里只是占位，避免快速结算查表炸掉
    "ttt":           dict(alpha=0.000, drop=9.990, beta=0.000, gamma=1.000),
}

# 冲刺时的角色加成：这是"冲刺列车把队长送到前排"在快速结算里的压缩形式。
# 完整引擎靠终盘抢位模拟出这件事，这里直接给结果。
SPRINT_ROLE_BONUS = {
    Role.SPRINTER: 1.045, Role.LEADOUT: 1.005, Role.LEADER: 1.000,
    Role.ROULEUR: 0.988, Role.BREAKAWAY: 0.984, Role.CLIMBER: 0.978,
    Role.DOMESTIQUE: 0.980,
}

# 集团冲刺的混乱度。**这个数字之前是 0，那是一个严重的 bug。**
# 原来的排序完全由冲刺速度决定，没有任何随机，结果是全世界最快的那个人
# 赢下他参加的每一场平路赛段——25 个赛季跑下来，一名车手拿了 233 场冠军，
# 其中 177 场是大环赛赛段。现实里最好的冲刺手一年赢十几场，而不是每场都赢：
# 抢位、被关、列车散架、前面有人摔，这些才是冲刺的常态。
SPRINT_CHAOS = 0.052

# 抢位能力对冲刺结果的影响幅度（positioning 0→100 映射到 ±6%）。
# 加这一项还有个附带好处：positioning 这条属性原本只在完整引擎里有用，
# 快速结算完全无视它——同一条属性在两套结算里意义不同，是设计上的裂缝。
POSITION_EDGE = 0.0012

# 「跟住了但已经很勉强」在终点冲刺时要付出多少代价。
# 平路上几乎没有代价（跟住就是跟住），但翻过四千米爬升之后，
# 那个只是刚好没掉队的人不可能再赢冲刺——所以陡的地形系数大得多。
# 这是丘陵赛段 66% 被纯冲刺手拿下的直接原因。
FATIGUE_AT_LINE = {
    "flat": 0.25, "hilly": 1.70, "cobbled": 2.10,
    "mountain": 2.60, "summit_finish": 2.80, "itt": 0.0, "ttt": 0.0,
}

# 突围专家「跑进正确的那个突围」的概率，以及跑进去之后的能力加成。
# 之前给的是 1 + random×0.012（万分之几），等于没给：25 个赛季里
# 突围手 per100 只有 4.8，是所有角色里最低的一半——这个角色在游戏里
# 不存在。突围本来就该是「多数时候被抓回，偶尔成功一整天」的形状，
# 那是一次抽奖，不是一点点平均加成。
# 石板路的数字特别高，是对着完整引擎调出来的：完整引擎在石板赛道上
# 跑出来的前十里有五个突围手——石板路是一场消耗战，能一路自己顶到底
# 的人才留在前面，这正是突围手的能力画像。
BREAKAWAY_LUCK = {
    "flat": 0.05, "hilly": 0.14, "cobbled": 0.55,
    "mountain": 0.16, "summit_finish": 0.10, "itt": 0.0, "ttt": 0.0,
}
BREAKAWAY_GAIN = 0.052

# 冠军单飞夺冠的额外优势（占冠军成绩的比例）。
# 山地赛段的冠军通常是攻击出去单独过线的，他领先的那一分多钟不来自
# 能力差，而来自"他选择了进攻"。不显式给出来的话，快速结算算出的
# 山顶终点会变成一群人前后脚到达，完全没有戏剧性。
SOLO_WIN_BONUS = {
    "flat": 0.0, "cobbled": 0.0015, "hilly": 0.0025,
    "mountain": 0.0055, "summit_finish": 0.0060, "itt": 0.0, "ttt": 0.0,
}

# 发挥波动分成两层，这是多日赛能不能跑对的关键：
#
#   赛事级 —— 车手来到这场比赛时的状态，整场不变（RACE_SIGMA）
#   赛段级 —— 具体某一天的好坏，每天重掷（DAILY_SIGMA）
#
# 单个赛段看，两者叠加后的总波动和完整引擎对得上。但在 21 天的大环赛里
# 赛段级噪声会互相抵消，赛事级不会——这正是现实：总成绩前几名的差距来自
# 真实实力和一两次崩盘，而不是每天随机丢一分钟。
# 早期版本只有单层噪声，跑出来亚军落后十五分钟，而真实数字是二到八分钟。
RACE_SIGMA = {
    "flat": 0.024, "cobbled": 0.038, "hilly": 0.030,
    "mountain": 0.030, "summit_finish": 0.030, "itt": 0.020, "ttt": 0.014,
}

DAILY_SIGMA = {
    "flat": 0.025, "cobbled": 0.040, "hilly": 0.033,
    "mountain": 0.023, "summit_finish": 0.023, "itt": 0.020, "ttt": 0.012,
}


# --------------------------------------------------------------------------
# 能力评估
# --------------------------------------------------------------------------

def _climb_merit(r: Rider) -> float:
    """爬坡能力：每公斤（含车）的持续功率。爬坡时间与它成反比。"""
    return r.params.cp_climb / r.params.total_mass


def _flat_merit(r: Rider) -> float:
    """平路生存能力：在集团里跟到终点的能力，不是冲刺能力。"""
    return r.params.cp / (r.params.total_mass ** 0.35 * r.params.cda_hoods)


def _sprint_merit(r: Rider) -> float:
    """冲刺速度：终点前 200 米能骑多快，正比于 (功率/迎风面积)^(1/3)。"""
    peak = r.params.cp + r.params.peak_anaerobic
    return (peak / (r.params.cda_hoods * 0.86)) ** (1 / 3)


def _itt_merit(r: Rider) -> float:
    """计时赛速度，同样是 (功率/迎风面积)^(1/3)。"""
    return (r.params.cp / r.params.cda_aero) ** (1 / 3)


def stage_merit(r: Rider, stage_type: str, climb_share: float) -> float:
    """综合能力值。越大越强，比值有物理含义（差 5% 就是慢 5%）。"""
    if stage_type == "itt":
        return _itt_merit(r)
    if stage_type == "cobbled":
        # 石板路吃的是绝对功率和抗颠簸，体重轻**是劣势**。
        #
        # `_flat_merit` 里除以 mass^0.35，那是平路的账：轻的人推得动。
        # 石板路正相反——颠簸里要靠体重压住车、靠绝对功率碾过去，
        # 六十公斤的爬坡手在这里会被抖散。所以先把平路那个体重优势抵消掉，
        # 再按体重给一点正向加成。完整引擎跑出来的前十里没有一个轻车手，
        # 全是突围型和工兵，这一条就是为了对上那个结果。
        pen = 0.972 if r.role in (Role.SPRINTER, Role.LEADOUT) else 1.0
        bulk = (r.params.total_mass / 74.0) ** 0.55
        return (_flat_merit(r) * bulk
                * (0.72 + 0.0056 * r.attributes.resilience) * pen)
    climb = _climb_merit(r)
    flat = _flat_merit(r)
    # 用赛道的"爬坡时间占比"在两种能力之间插值：
    # 这一个数字就把平路赛段和山顶终点的差别说清楚了
    return (climb ** climb_share) * (flat ** (1.0 - climb_share)) \
        * (1.0 + 0.0006 * (r.attributes.endurance - 50))


def climb_share(course: Course) -> float:
    """赛道中"由每公斤功率决定"的时间占比，0-1。

    用累计爬升除以里程做代理：平路赛段约 4 m/km，
    高山赛段可达 25-30 m/km。
    """
    ascent_per_km = course.total_ascent_m / max(1.0, course.length_m / 1000)
    return max(0.0, min(0.85, (ascent_per_km - 2.0) / 30.0))


# --------------------------------------------------------------------------
# 冠军成绩
# --------------------------------------------------------------------------

def winner_speed_kmh(course: Course, stage_type: str,
                     field_strength: float = 1.0) -> float:
    """冠军均速。系数由完整引擎在四种赛道上的实测结果拟合。"""
    ascent_per_km = course.total_ascent_m / max(1.0, course.length_m / 1000)
    # 开方而不是线性：从平路到丘陵的降速很快，从高山到更高山反而变化不大。
    # 系数由完整引擎在五条赛道上的实测拟合（平路/石板/丘陵/山地/计时）。
    # 集团速度模型重做之后重新拟合（平路 40.7 / 山地 30.3 / 石板 41.7 km/h）
    v = 47.00 - 3.22 * ascent_per_km ** 0.5
    if stage_type == "itt":
        v += 7.2                   # 气动姿势 + 全程独走，没有集团的走走停停
    elif stage_type == "hilly":
        v -= 0.8                   # 短而陡的坡要反复变速，比累计爬升显示的更累
    v *= field_strength
    return max(22.0, v)


# --------------------------------------------------------------------------
# 结算
# --------------------------------------------------------------------------

# 每名车手在一个赛段里出意外的概率，以及意外造成的时间损失区间。
# 数值与完整引擎的 incidents 模块标定到同一个量级：干燥公路约 6%，
# 雨天和石板路翻倍以上。
INCIDENT_RATE = {
    "flat": 0.055, "hilly": 0.055, "mountain": 0.045,
    "summit_finish": 0.045, "cobbled": 0.140, "itt": 0.020, "ttt": 0.030,
}


def incident_loss(rng: random.Random, stage_type: str, rain: float) -> float:
    """掷一次意外，返回损失的秒数（0 表示没事）。

    快速结算里不模拟"停在路边等队车"的过程，只把结果折算成时间损失——
    但概率和量级必须和完整引擎对得上，否则玩家会发现自己亲自看的比赛
    摔车不断，系统代跑的比赛却风平浪静。
    """
    rate = INCIDENT_RATE[stage_type] * (1.0 + rain * 1.6)
    if rng.random() > rate:
        return 0.0
    r = rng.random()
    if r < 0.55:
        return rng.uniform(18, 60)      # 爆胎，换个轮子追回来
    if r < 0.88:
        return rng.uniform(60, 200)     # 轻微摔车或掉队追不上
    return rng.uniform(200, 900)        # 严重摔车


# --------------------------------------------------------------------------
# 战术：把 Directive 的旋钮翻译成快速结算里的数
# --------------------------------------------------------------------------
#
# **在这之前，玩家选的打法完全没有生效。** career 每场比赛都算了一份
# orders，然后原样扔掉——run_event 根本没有这个参数，快速结算也不知道
# 「指令」这回事。完整引擎认得 Directive（verify_orders.py 一直在证明
# 这一点），但赛季里跑的是快速结算。玩家唯一的战术杠杆是空的。
#
# 下面这四条是「战术在闭式解里的全部含义」，每一条都对应一个物理现实：
#
#   领骑要顶风  —— pull_bias 越高，自己越累，赢的机会越小
#   有人顶风就有人省力 —— 队友顶的风加起来，喂给被保护的那个人
#   舍得烧 / 舍不得烧 —— spend_bias 与 conserve 直接改今天的输出
#   冲刺是另一回事 —— sprint_bias 只动终点那 200 米，不动全程能力
#
# 系数都很小，因为 merit 差 1% 就是慢 1%——这在自行车里已经是决定性的。

# 代价是相对 pull_bias=1.0 算的，不是相对 0。1.0 的含义是「正常轮换领骑」，
# 那是这套模型的基准状态，不该被扣分——否则「自由发挥」这条指令会凭空
# 变成一个负面选项，而它本该是中性的那一个。
PULL_COST = 0.0055        # 偏离正常领骑量，每一点的能力代价
SHELTER_GAIN = 0.0030     # 队友每顶一点风，被保护者拿到的收益
SHELTER_CAP = 12.0        # 收益封顶：第九个工兵不会再让队长快多少
SPEND_GAIN = 0.022        # spend_bias 每高出 1.0 一点的收益
CONSERVE_COST = 0.030     # conserve 的代价，换的是后面赛段的体力
SPRINT_SWING = 0.075      # sprint_bias 对终点冲刺分的影响幅度
ATTACK_SIGMA = 0.006      # attack_bias 带来的额外波动：进攻要么成要么崩


def _is_protected(d) -> bool:
    """判断一名车手是不是「今天被保护的那个」。

    不能只看「没在领骑」——那样全队保存体力时人人都能拿到掩护收益。
    真正的标志是：他不顶风，而且他在为终点留着（要冲刺，或者在省力）。
    """
    return d.pull_bias <= 0.5 and (d.sprint_bias >= 1.0 or d.conserve > 0.0)


def tactic_factors(riders, directives, stage_type: str = "flat"
                   ) -> dict[str, tuple[float, float, float]]:
    """返回 {车手 id: (能力倍率, 冲刺倍率, 额外波动)}。

    掩护收益按车队结算：一支队顶的风只有那么多，保护两个人就每人一半。
    这正是经理游戏的核心取舍——**围绕一个人建队，还是两头下注。**
    """
    by_team: dict[str, list] = {}
    for r in riders:
        by_team.setdefault(r.team_id or "", []).append(r)

    # 计时赛里没有集团，没有风可挡，也没有列车可带。领骑、掩护、冲刺
    # 这三件事在这里全都不存在——只剩「今天舍不舍得把自己榨干」。
    # 不做这个区分的话，一支工兵成群的队会在计时赛里凭空得到掩护收益。
    solo = stage_type == "itt"

    out: dict[str, tuple[float, float, float]] = {}
    for tid, squad in by_team.items():
        support = sum(directives[r.rider_id].pull_bias for r in squad
                      if not _is_protected(directives[r.rider_id]))
        prot = [r for r in squad if _is_protected(directives[r.rider_id])]
        share = min(support, SHELTER_CAP) / max(1, len(prot)) if prot else 0.0
        for r in squad:
            d = directives[r.rider_id]
            m = 1.0 + SPEND_GAIN * (d.spend_bias - 1.0)
            m -= CONSERVE_COST * d.conserve
            if not solo:
                m -= PULL_COST * (d.pull_bias - 1.0)
                if _is_protected(d):
                    m += SHELTER_GAIN * share
            out[r.rider_id] = (
                m,
                1.0 if solo else 1.0 + SPRINT_SWING * (d.sprint_bias - 1.0),
                ATTACK_SIGMA * d.attack_bias)
    return out


@dataclass
class QuickStage:
    order: list[str]
    times: dict[str, float]
    dnf: list[str]


def resolve(riders: list[Rider], course: Course, stage_type: str,
            rng: random.Random,
            form: dict[str, float] | None = None,
            rain: float = 0.0,
            time_limit_frac: float = 1.16,
            directives: dict | None = None) -> QuickStage:
    """把一个赛段一次算完。

    form 是每名车手当天的状态倍率（疲劳、士气都折算进来），默认全 1.0。
    directives 是每名车手的战术旋钮；不给就没有战术，所有人自由发挥。
    """
    share = climb_share(course)
    shape = GAP_SHAPE[stage_type]
    sigma = DAILY_SIGMA[stage_type]
    form = form or {}

    tac = tactic_factors(riders, directives, stage_type) if directives else {}

    scored: list[tuple[float, float, Rider]] = []
    for r in riders:
        tm, ts, tv = tac.get(r.rider_id, (1.0, 1.0, 0.0))
        merit = stage_merit(r, stage_type, share)
        merit *= form.get(r.rider_id, 1.0)
        merit *= tm
        merit *= math.exp(rng.gauss(0.0, sigma + tv))
        # 突围抽奖：绝大多数日子里什么都不会发生，中奖的那天他真的能赢
        luck = BREAKAWAY_LUCK.get(stage_type, 0.0)
        if r.role is not Role.BREAKAWAY:
            luck *= 0.0
        # 被下令抢突围的人，不管什么角色都在赌这一把
        if directives and directives[r.rider_id].attack_bias >= 2.5:
            luck = max(luck, BREAKAWAY_LUCK.get(stage_type, 0.0) * 0.75)
        if luck and rng.random() < luck:
            merit *= 1.0 + BREAKAWAY_GAIN * (0.5 + rng.random())
        scored.append((merit, _sprint_merit(r) * ts, r))

    scored.sort(key=lambda x: -x[0])
    best = scored[0][0]

    v = winner_speed_kmh(course, stage_type)
    winner_time = course.length_m / (v / 3.6)

    times: dict[str, float] = {}

    if stage_type == "itt":
        # 计时赛：时间 = 距离 / 速度。指数 0.72 是对完整引擎的修正——
        # 引擎里弱一些的车手会把配速压得更接近自己的极限，实际差距
        # 比纯能力比值算出来的要小。
        for merit, _, r in scored:
            times[r.rider_id] = winner_time * (best / merit) ** 0.72
    else:
        bunch: list[tuple[float, float, Rider]] = []
        for merit, sprint, r in scored:
            rel = (best - merit) / best
            excess = max(0.0, rel - shape["drop"])
            loss = shape["alpha"] * rel
            if excess > 0:
                loss += shape["beta"] * excess ** shape["gamma"]
            if loss <= 1e-9:
                bunch.append((rel, sprint, r))     # 跟住了，同一时间过线
            else:
                times[r.rider_id] = winner_time * (1.0 + loss)

        # 集团内名次由冲刺决定：速度 × 角色（能不能占到前排）
        #   × 抢位 × 到线时的余量 × 冲刺本身的混乱
        fat = FATIGUE_AT_LINE.get(stage_type, 0.25)
        bunch.sort(key=lambda x: -(
            x[1] * SPRINT_ROLE_BONUS[x[2].role]
            * max(0.30, 1.0 - x[0] * fat)
            * (0.94 + POSITION_EDGE * x[2].attributes.positioning)
            * math.exp(rng.gauss(0.0, SPRINT_CHAOS))))
        for _, _, r in bunch:
            # **同一个集团判同一时间。** 这是公路车的基本规则，而不是简化。
            #
            # 之前这里给的是 winner_time + i×0.06，本意是「名次差不影响
            # 总成绩」——但它影响了：21 个赛段累积一秒多，而短分站赛的
            # 总成绩前二本来就只差几秒。跑 25 个赛季，冲刺手拿走了 30%
            # 的总成绩冠军，亚军平均只落后 6.6 秒，最小 0.0 秒。**那不是
            # 比赛结果，是浮点数排序的副产品。**
            #
            # 名次由 order 承载（Python 的排序是稳定的，插入顺序就是冲刺
            # 顺序），总成绩由奖励秒数拉开——10/6/4 秒，UCI 的真实规则，
            # classification 里一直写着，只是从来没起过作用，因为每个人的
            # 时间本来就不一样。修好之后，短分站赛由谁收集奖励秒数决定，
            # 那才是现实里的样子。
            times[r.rider_id] = winner_time

    # 意外：爆胎、摔车。放在冠军判定之前，所以领骑的人也可能因为
    # 一次爆胎丢掉胜利——这在现实中每个赛季都会发生几次。
    for r in riders:
        loss = incident_loss(rng, stage_type, rain)
        if loss and r.rider_id in times:
            times[r.rider_id] += loss

    order = sorted(times, key=lambda rid: times[rid])

    # 冠军进攻带来的额外领先。只加在"没跟住的人"头上：山地赛段里总成绩
    # 前几名通常是一起过线的，冠军甩开的是集团，不是身边这几个对手。
    # 早期版本无差别加时，结果 21 天大环赛打完亚军落后十五分钟——
    # 真实数字是两到八分钟。
    bonus = SOLO_WIN_BONUS[stage_type] * winner_time
    if bonus > 0 and rng.random() < 0.55:
        front = times[order[0]]
        for rid in order[1:]:
            if times[rid] > front + 1.0:
                times[rid] += bonus

    # 关门时间
    cutoff = times[order[0]] * time_limit_frac
    dnf = [rid for rid in order if times[rid] > cutoff]
    order = [rid for rid in order if rid not in dnf]
    return QuickStage(order=order, times=times, dnf=dnf)
