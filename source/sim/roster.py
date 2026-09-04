"""车手与车队生成。

原型阶段用来快速造出一个 150 人的集团。正式版会被数据表替换，
但原型的属性分布应当直接沿用——它决定了"50 分是什么水平"。
"""

from __future__ import annotations

import random

from .rider import Attributes, Rider, Role

# 原型：(角色, 体重区间, 各属性的均值偏移)
ARCHETYPES: dict[Role, dict] = {
    # 纯爬坡手要为「纯」付出代价。原来他爬得最好，耐力和恢复还都是正的——
    # 那他就是一个更便宜、更强的总成绩核心，后者没有任何存在理由。
    #
    # 现实里的纯爬坡手赢的是山地赛段，不是三周大环赛：他在第二周会掉下去。
    # 把耐力和恢复压到负值，游戏里那套逐日累积的疲劳就会替我们表达这件事，
    # 不需要额外写一条「爬坡手第 14 天开始变弱」的规则。
    Role.CLIMBER: dict(
        mass=(56, 64),
        offs=dict(climbing=+26, flat=-6, sprint=-18, time_trial=-4,
                  descending=+2, endurance=+2, recovery=-6, resilience=-6),
    ),
    # 总成绩核心原来是「样样都行」：爬坡 +18（比纯爬坡手低 8）、体重还多
    # 四公斤，于是每公斤功率打不过爬坡手；计时 +16 又打不过全能型的专精。
    # 25 个赛季的数据把结果摆在那儿——他的 per100 是全部角色里最低的，
    # 总成绩冠军里爬坡手拿 41%，他只有个位数。**一个赢不了任何东西的角色，
    # 却是队里最贵的那个人，这是设计上的自相矛盾。**
    #
    # 现实里的大环赛冠军不是全能型，是「会计时赛的顶级爬坡手」：
    # 爬坡拉到接近纯爬坡手，体重压下来，再加上计时和恢复。
    # 他和爬坡手的区别不在爬得慢一点，而在三周里不会崩。
    # 爬坡试过拉到 +27（比纯爬坡手还高），大环赛总成绩确实到手了，
    # 但连山地赛段也一起拿走了 66%，爬坡手只剩 27%——那等于把一个角色
    # 换成了另一个角色。收回 +24：**总成绩核心比爬坡手爬得略差一点，
    # 但他计时快、耐力和恢复高得多，赢的是三周，不是一天。**
    # 真正让他当上大环赛冠军的不是这三分爬坡，是修好了休赛期的角色重排。
    Role.LEADER: dict(
        mass=(58, 66),
        offs=dict(climbing=+24, flat=+4, sprint=-12, time_trial=+16,
                  descending=+6, endurance=+16, recovery=+12,
                  positioning=+8, resilience=+14),
    ),
    Role.SPRINTER: dict(
        mass=(70, 82),
        offs=dict(climbing=-28, flat=+10, sprint=+30, time_trial=-2,
                  descending=0, endurance=-8, recovery=+2, positioning=+14),
    ),
    Role.LEADOUT: dict(
        mass=(68, 78),
        offs=dict(climbing=-20, flat=+14, sprint=+10, time_trial=+6,
                  positioning=+18, endurance=-2),
    ),
    Role.ROULEUR: dict(
        mass=(70, 80),
        offs=dict(climbing=-14, flat=+18, sprint=-4, time_trial=+14,
                  endurance=+8, resilience=+8),
    ),
    Role.BREAKAWAY: dict(
        mass=(64, 72),
        offs=dict(climbing=+6, flat=+8, sprint=-6, time_trial=+8,
                  endurance=+12, recovery=+8, resilience=+16),
    ),
    Role.DOMESTIQUE: dict(
        mass=(66, 76),
        offs=dict(flat=+4, endurance=+6, recovery=+4),
    ),
}

# 一支八人队的典型构成
TEAM_TEMPLATE = [
    Role.LEADER, Role.CLIMBER, Role.SPRINTER, Role.LEADOUT,
    Role.ROULEUR, Role.BREAKAWAY, Role.DOMESTIQUE, Role.DOMESTIQUE,
]


def make_rider(rider_id: str, name: str, team_id: str, role: Role,
               tier: float, rng: random.Random) -> Rider:
    """tier: 0-1，队伍档次。0 = 洲际队，1 = 世巡赛顶级队。"""
    spec = ARCHETYPES[role]
    base = 34 + 34 * tier                      # 34 -> 68 的属性基准
    mass = rng.uniform(*spec["mass"])
    offs = spec["offs"]

    def roll(key: str) -> int:
        return int(round(base + offs.get(key, 0) + rng.gauss(0, 6)))

    attrs = Attributes(
        flat=roll("flat"), climbing=roll("climbing"), sprint=roll("sprint"),
        time_trial=roll("time_trial"), descending=roll("descending"),
        endurance=roll("endurance"), recovery=roll("recovery"),
        positioning=roll("positioning"), resilience=roll("resilience"),
    ).clamped()

    return Rider(
        rider_id=rider_id, name=name, team_id=team_id,
        body_mass_kg=round(mass, 1), attributes=attrs, role=role,
        form=round(rng.gauss(1.0, 0.035), 3),
        morale=round(rng.uniform(0.92, 1.08), 3),
    )


def build_peloton(n_teams: int = 20, seed: int = 7,
                  spread: float = 0.62) -> list[Rider]:
    """生成 n_teams 支八人队。队伍档次从顶到底线性分布。

    `spread` 是最强队到最弱队的档次落差。默认 0.62 覆盖世巡赛到洲际队，
    那是「世界」的样子；但**一场世巡赛不会邀请洲际队**，用这个默认值去
    对标世巡赛的实测数据是拿错了赛场。

    这件事我是绕了一大圈才发现的：平路赛段跑出来只有 8% 的车手在冠军
    一分钟内完赛，而现实的平路赛段是九成人同一时间过线。查了破风系数、
    查了集团分裂阈值、查了差距分布的拟合，全都不是——把车队档次落差收到
    0.18（只留世巡赛级别）之后，同一套引擎立刻给出 92%。
    **集团炸开不是因为模型错了，是因为集团里真的混进了骑不动这个速度的人。**
    """
    rng = random.Random(seed)
    riders: list[Rider] = []
    for t in range(n_teams):
        tier = 1.0 - t / max(1, n_teams - 1) * spread
        team_id = f"T{t + 1:02d}"
        for i, role in enumerate(TEAM_TEMPLATE):
            rid = f"{team_id}-{i + 1}"
            riders.append(make_rider(rid, f"{team_id}号车手{i + 1}", team_id,
                                     role, tier, rng))
    return riders
