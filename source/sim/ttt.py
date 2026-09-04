"""团体计时赛：这套引擎里最后一个还没有物理表达的赛段类型。

个人计时赛是「一个人对着风骑」，团体计时赛是**一队人轮流对着风骑**——
听起来只差一点，模型上完全是另一回事，因为它同时受三件事支配：

  轮转  —— 八个人里只有一个在顶风，每人轮到 1/N 的时间
  掉队  —— 跟不上的人被放掉，剩下的人轮转更频繁，于是更累，于是更慢
  计时  —— 成绩取第 N 个人过线的时间，不是第一个

第三条是全部戏剧性的来源。**一支队不是「跑得多快」，是「能带着几个人
跑多快」。** 把最弱的两个人榨干甩掉能提速，但如果计时位是第五人，
甩掉第四个人就等于自杀。这是一个真实的、玩家能看懂的取舍，也是这个
赛段类型值得单独建模的唯一理由——否则它就只是「打了折的个人计时赛」。

### 为什么不用主赛程那台引擎

`race.py` 的核心是「集团怎么形成、谁跟得住谁」，那套逻辑在团体计时赛里
全都不适用：这里没有集团博弈，没有战术，没有攻击，队伍之间也不互相影响
（每支队隔几分钟单独出发）。硬套进去要关掉一半的机制，还不如照着团体
计时赛真正的物理写一份短得多的：**轮转、掉队、取第 N 人。**

物理本身仍然复用 `physics` 和 `energy`，所以功率、风阻、W′ 消耗和
主引擎是同一套账，不会出现「团体计时赛里的瓦特和别处不是一回事」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sim import physics
from sim.energy import EnergyState
from sim.course import SURFACE_CRR
from sim.rider import Rider

# 轮转一次的时长（秒）。真实团体计时赛的轮转是 15–30 秒一换，
# 短了太乱，长了顶风的人会崩。
PULL_S = 20.0

# 队列里各个位置的破风系数。团体计时赛排的是紧密单列，
# 比公路集团省得多——第二位就只剩 62%，第四位之后基本到底。
_TTT_DRAFT = [1.00, 0.62, 0.55, 0.51, 0.49, 0.48, 0.475, 0.47]
_TTT_DEEP = 0.47

# 目标强度：团体计时赛全程压在 CP 稍上方，靠 W′ 一点点垫。
# 比个人计时赛更狠，因为每人只有 1/N 的时间在顶风。
TARGET_INTENSITY = 1.06

# 掉队判定：连续这么多秒需要的功率超过他当下能给的上限，就放掉。
# 给一段缓冲是因为轮转本身会造成瞬时超限，一秒一判会把整队判散。
DROP_GRACE_S = 12.0


def ttt_stage(name: str = "团体计时赛", length_km: float = 28.0):
    """一条典型的团体计时赛赛道：平、直、有风。

    真实的团体计时赛几乎不上山——爬坡会立刻把队伍拆散，主办方要的是
    「八个人整整齐齐冲过终点」的画面。
    """
    from sim.course import Course, Segment, StageType
    m = length_km * 1000
    segs = [
        Segment(m * 0.35, 0.002, headwind=2.5, name="逆风出发段"),
        Segment(m * 0.20, 0.012, name="缓上坡"),
        Segment(m * 0.15, -0.010, technical=0.15, name="缓下坡"),
        Segment(m * 0.30, 0.001, headwind=-2.0, name="顺风冲线段"),
    ]
    return Course(name, segs, StageType.TTT, start_altitude_m=60)


def _crr(seg) -> float:
    """路面滚阻。团体计时赛几乎都在柏油上，但赛道数据允许别的路面。"""
    return 0.0042 + SURFACE_CRR.get(seg.surface, 0.0)


def draft_at(pos: int) -> float:
    return _TTT_DRAFT[pos] if pos < len(_TTT_DRAFT) else _TTT_DEEP


@dataclass
class TttResult:
    team_id: str
    time_s: float
    finishers: list[str]              # 到达计时位的人（按顺序）
    dropped: list[tuple[str, float]]  # (车手, 掉队时的已走米数)
    avg_speed_kmh: float
    log: list[str] = field(default_factory=list)


def _team_speed(states, course, distance_m: float, dt: float,
                v_prev: float, need: int) -> float:
    """解出这一步全队能跑多快。

    和主引擎同一个思路：**集团的速度由全员的平均每人成本决定**，
    不是由最前面那个人决定。区别只在轮转让「平均破风系数」由队伍人数
    直接给出——八个人时平均是 0.53，掉到四个人就变成 0.67，
    每人要多付四分之一的风阻。这一个数字就解释了为什么掉队是灾难性的。
    """
    n = len(states)
    if n == 0:
        return v_prev
    seg = course.segment_at(distance_m)
    grade = seg.grade
    rho = physics.air_density(course.altitude_at(distance_m))

    avg_draft = sum(draft_at(i) for i in range(n)) / n
    avg_cda = sum(s.rider.params.cda_aero for s in states) / n * avg_draft
    avg_mass = sum(s.rider.params.total_mass for s in states) / n
    # **配速由「必须活到终点的那几个人」决定，不是全队平均。**
    #
    # 第一版按全队平均 CP 配速，结果每支队都恰好剩下 4 个人——因为配速
    # 定义成「平均那个人刚好到极限」，弱于平均的一半必然被榨干。那不是
    # 模拟，那是同义反复，而且所有队伍长得一模一样。
    #
    # 真实的团体计时赛不这么骑：教练看着计时位那个人的功率计定速。
    # 改成取当前还在队里最强的 need 个人的平均 CP 之后，八个人水平接近的
    # 队能整整齐齐到线，四强四弱的队跑得更快但只剩四个——**阵容厚度
    # 第一次成了一个有代价的选择。**
    # 取「必须活到终点的那个最弱的人」的 CP，而不是核心几人的平均。
    # 教练盯着的就是计时位那块功率计——跑得比他还快没有意义，
    # 到线的是第 need 个人。
    ranked = sorted(states, key=lambda s: -s.energy.effective_cp())
    pace_man = ranked[min(need, len(ranked)) - 1]
    budget = pace_man.energy.effective_cp() * TARGET_INTENSITY

    return physics.advance_speed(
        v_prev, budget, dt, total_mass=avg_mass, grade=grade,
        cda=avg_cda, crr=_crr(seg), rho=rho,
        headwind=seg.headwind, max_speed=25.0)


@dataclass
class _S:
    rider: Rider
    energy: EnergyState
    over_s: float = 0.0
    front_s: float = 0.0


def team_time_trial(riders: list[Rider], course, count_nth: int = 4,
                    dt: float = 1.0) -> TttResult:
    """跑一支队的团体计时赛。

    count_nth：取第几个人过线的成绩。真实规则通常是第 4 或第 5 人
    （八人出发）。这个数字直接决定了「能不能榨干队尾」的取舍。
    """
    states = [_S(r, EnergyState(
        cp=r.params.cp, w_prime=r.params.w_prime,
        durability=getattr(r.params, "grit", 1.0))) for r in riders]
    alive = list(states)
    dropped: list[tuple[str, float]] = []
    log: list[str] = []

    d = 0.0
    t = 0.0
    v = 12.0
    need = min(count_nth, len(riders))

    while d < course.length_m and alive and t < 3 * 3600:
        v = _team_speed(alive, course, d, dt, v, need)
        seg = course.segment_at(d)
        rho = physics.air_density(course.altitude_at(d))

        # 轮转：**强者拉长班，弱者拉短班。**
        #
        # 平均轮转（每人 1/N 的时间）会让任何弱于配速者的人必然被榨干，
        # 于是每支队都恰好剩下 need 个人——所有队伍长得一模一样，
        # 阵容厚度毫无意义。真实的团体计时赛里领骑时间是按能力分配的：
        # 强的人拉一分钟，弱的人上去露个头就下来。加上这一条之后，
        # 一个比配速者弱一截的人也能靠短班活到终点。
        tot_cp = sum(s.energy.effective_cp() for s in alive) or 1.0
        lead = max(range(len(alive)), key=lambda i: (
            alive[i].energy.effective_cp() / tot_cp * t - alive[i].front_s))
        alive[lead].front_s += dt
        for i, s in enumerate(alive):
            pos = (i - lead) % len(alive)
            p = physics.power_required(
                v, s.rider.params.total_mass, seg.grade,
                s.rider.params.cda_aero * draft_at(pos),
                _crr(seg), rho, seg.headwind)
            p = max(0.0, p)
            cap = s.energy.max_power(s.rider.params.peak_anaerobic)
            if p > cap:
                s.over_s += dt
                p = cap
            else:
                s.over_s = max(0.0, s.over_s - dt * 0.5)
            s.energy.update(p, dt)

        # 放掉跟不住的人。**这不是惩罚，是团体计时赛的常规操作**——
        # 队尾被榨干之后主动放走，剩下的人轮转虽然更频繁，但不必再为
        # 他减速。**队伍永远不会掉到计时位以下**：那几个人必须到线，
        # 哪怕已经骑在自己的极限之上——现实里也是这样，最后那几公里
        # 队长在前面喊的就是「再撑一点」。
        if len(alive) > need:
            worst = max(alive, key=lambda s: s.over_s)
            if worst.over_s >= DROP_GRACE_S:
                alive.remove(worst)
                dropped.append((worst.rider.rider_id, d))
                log.append(f"{worst.rider.name} 在 {d / 1000:.1f} km 处掉队"
                           f"（队伍剩 {len(alive)} 人）")

        d += v * dt
        t += dt

    speed = (course.length_m / t * 3.6) if t > 0 else 0.0
    return TttResult(
        team_id=riders[0].team_id if riders else "",
        time_s=t,
        finishers=[s.rider.rider_id for s in alive],
        dropped=dropped,
        avg_speed_kmh=speed,
        log=log,
    )
