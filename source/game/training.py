"""训练：把「培养一名车手」变成一个可执行的跨赛季计划。

`management.develop()` 早就支持指定训练重点了，但玩家碰不到它——只能
看着车手按默认曲线自己长。这一层把它接出来。

**训练必须是取舍，不能是白送的成长。** 一个只会让人变强的系统，玩家
点一次之后就再也不用想了。所以这里每个旋钮都有代价：

  专项训练  —— 重点属性涨得快，其余属性几乎不涨。把一个全能手练成
              纯爬坡手，意味着他的平路和冲刺会相对退步
  训练强度  —— 大强度涨得更快，但赛季开始时带着疲劳，而且伤病风险翻倍
  集训营    —— 花钱买成长，但占掉休赛期的恢复时间

**收益要跨赛季才看得见。** 一个 22 岁的工兵练成爬坡手要三到四个赛季，
中途你会怀疑自己是不是选错了。这个等待本身就是经理游戏最好的钩子。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from game.world import RiderProfile
from sim.rider import Role


class Intensity(str, Enum):
    LIGHT = "轻量"
    NORMAL = "常规"
    HEAVY = "大强度"


# 强度对成长、赛季初疲劳、受伤风险的影响
INTENSITY = {
    Intensity.LIGHT:  dict(gain=0.85, fatigue=0.00, injury=0.5),
    Intensity.NORMAL: dict(gain=1.00, fatigue=0.06, injury=1.0),
    Intensity.HEAVY:  dict(gain=1.38, fatigue=0.18, injury=2.2),
}


@dataclass
class Program:
    """一套训练方案。"""

    key: str
    name: str
    focus: list[str]
    blurb: str


# 专项方案。刻意只给三到四个重点属性——「什么都练」等于什么都不练，
# 这是 develop() 里 focus 权重 1.35 / 0.75 决定的。
PROGRAMS: list[Program] = [
    Program("climb", "爬坡专项", ["climbing", "endurance", "recovery"],
            "把人往山里练。平路和冲刺会相对落下，但山地赛段的价值最高。"),
    Program("sprint", "冲刺专项", ["sprint", "flat", "positioning"],
            "爆发力天赋成分最高，练起来最慢，但冲刺手是曝光最多的角色。"),
    Program("tt", "计时专项", ["time_trial", "flat", "endurance"],
            "见效最快的一项——气动姿势和持续功率都练得动。"),
    Program("allround", "全面发展", ["flat", "endurance", "climbing",
                                     "positioning"],
            "不做取舍，适合还没定型的年轻人和不确定角色的车手。"),
    Program("recover", "恢复调整", ["recovery", "resilience", "endurance"],
            "成长最慢，但会显著压低疲劳和受伤风险。适合老将和伤后复出。"),
    Program("descend", "技术训练", ["descending", "positioning", "resilience"],
            "下坡和占位。这两项在成绩表上看不见，但决定了摔不摔车。"),
]

BY_KEY = {p.key: p for p in PROGRAMS}

# 按角色推荐的默认方案。玩家不管的人不会乱练。
DEFAULT_PROGRAM = {
    Role.LEADER: "allround", Role.CLIMBER: "climb", Role.SPRINTER: "sprint",
    Role.LEADOUT: "sprint", Role.ROULEUR: "tt", Role.BREAKAWAY: "allround",
    Role.DOMESTIQUE: "allround",
}


@dataclass
class Camp:
    """休赛期集训营。"""

    key: str
    name: str
    cost_per_rider: int          # 万元
    gain: float                  # 额外成长倍率
    attrs: list[str]
    fatigue: float
    blurb: str


CAMPS: list[Camp] = [
    Camp("none", "不集训", 0, 1.00, [], -0.05,
         "把休赛期还给车手。成长慢一点，但所有人都会以最好的状态开季。"),
    Camp("altitude", "高原集训", 18, 1.28, ["endurance", "recovery"], 0.10,
         "三周高原。有氧基础打得最扎实，代价是开季时还没完全恢复。"),
    Camp("wind", "风洞与计时营", 14, 1.20, ["time_trial", "positioning"], 0.04,
         "针对气动姿势。见效快，对计时赛和平路手价值最高。"),
    Camp("skills", "技术营", 10, 1.12, ["descending", "positioning",
                                        "resilience"], 0.02,
         "下坡线路、集团占位、湿滑路面。练的是不出事的能力。"),
]

CAMP_BY_KEY = {c.key: c for c in CAMPS}


@dataclass
class Plan:
    """一名车手的训练安排。存在车手档案上，跨赛季保留。"""

    program: str = "allround"
    intensity: Intensity = Intensity.NORMAL

    def to_dict(self) -> dict:
        return {"program": self.program, "intensity": self.intensity.value}

    @staticmethod
    def from_dict(d: dict) -> "Plan":
        return Plan(d.get("program", "allround"),
                    Intensity(d.get("intensity", "常规")))


def default_plan(rider: RiderProfile) -> Plan:
    return Plan(DEFAULT_PROGRAM.get(rider.role, "allround"), Intensity.NORMAL)


# --------------------------------------------------------------------------
# 结算
# --------------------------------------------------------------------------

@dataclass
class TrainingResult:
    rider_id: str
    rider_name: str
    changes: dict[str, int] = field(default_factory=dict)
    overall_delta: int = 0
    injured: bool = False
    note: str = ""


def apply_training(rider: RiderProfile, plan: Plan, camp: Camp,
                   race_days: int, rng: random.Random) -> TrainingResult:
    """把一个赛季的训练结果写进车手档案。

    在 management.develop() 之上叠加三件事：专项重点、强度、集训营。
    develop() 负责年龄曲线和潜力天花板，这里只负责「练什么、练多狠」。
    """
    from game.management import develop

    prog = BY_KEY.get(plan.program, BY_KEY["allround"])
    inten = INTENSITY[plan.intensity]

    focus = list(dict.fromkeys(prog.focus + camp.attrs))
    before = rider.overall

    # 强度与集训营都作用在成长率上。做法是临时放大潜力头顶空间——
    # 这样天花板仍然由潜力决定，训练只能让你更快够到它，不能突破它。
    mult = inten["gain"] * camp.gain
    real_pot = rider.potential
    rider.potential = min(99, int(rider.potential * (1.0 + (mult - 1.0) * 0.55)))
    changes = develop(rider, race_days, rng, focus=focus)
    rider.potential = real_pot

    rider.fatigue = round(min(1.0, max(0.0, rider.fatigue
                                       + inten["fatigue"] + camp.fatigue)), 3)

    res = TrainingResult(rider.rider_id, rider.name, changes,
                         rider.overall - before)

    # 大强度训练把人练伤：这是「练狠一点」唯一真实的代价
    injury_p = 0.045 * inten["injury"] * (1.0 + max(0, rider.age - 30) * 0.09)
    if rng.random() < injury_p:
        res.injured = True
        rider.form = round(max(0.86, rider.form - rng.uniform(0.04, 0.10)), 3)
        rider.fatigue = round(min(1.0, rider.fatigue + 0.25), 3)
        res.note = "训练中受伤，赛季初状态受影响"
    return res


def run_offseason_training(world, team_id: str, plans: dict[str, Plan],
                           camp_key: str, race_days: dict[str, int],
                           rng: random.Random) -> tuple[list[TrainingResult], int]:
    """给一支队跑一次休赛期训练。返回结果与花费。"""
    camp = CAMP_BY_KEY.get(camp_key, CAMP_BY_KEY["none"])
    roster = world.roster(team_id)
    results = []
    for r in roster:
        plan = plans.get(r.rider_id) or default_plan(r)
        results.append(apply_training(r, plan, camp,
                                      race_days.get(r.rider_id, 30), rng))
    return results, camp.cost_per_rider * len(roster)
