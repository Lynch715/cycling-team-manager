"""车手：设计属性 -> 物理参数。

游戏侧只维护 9 项 0-100 的属性（策划和玩家看得懂的东西），
物理侧需要的是 CP、W′、CdA、峰值功率这类工程量。这一层负责翻译。

翻译规则集中在这一个文件里，是为了以后调数值时只改一处：
只要 attributes -> params 的映射不变，引擎其余部分对数值改动完全无感。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .energy import EnergyState

BIKE_MASS = 7.2       # 整车含配件，UCI 下限 6.8kg
KIT_MASS = 1.3        # 头盔、鞋、衣服、水壶


class Role(str, Enum):
    """赛段角色。决定战术 AI 的行为倾向，不影响物理参数。"""

    LEADER = "leader"          # 总成绩核心
    CLIMBER = "climber"        # 爬坡手 / 山地副将
    SPRINTER = "sprinter"      # 冲刺手
    LEADOUT = "leadout"        # 冲刺列车
    ROULEUR = "rouleur"        # 平路发动机 / 全能副将
    DOMESTIQUE = "domestique"  # 工兵
    BREAKAWAY = "breakaway"    # 突围专家


@dataclass
class Attributes:
    """0-100 的设计属性。50 为职业队平均水平，100 为世界顶级。"""

    flat: int = 50          # 平路：持续功率基底
    climbing: int = 50      # 爬坡：陡坡上的功率加成
    sprint: int = 50        # 冲刺：峰值功率与无氧储备
    time_trial: int = 50    # 计时赛：气动姿势 + 独走配速能力
    descending: int = 50    # 下坡：极速上限与过弯损失
    endurance: int = 50     # 耐力：长时间后的功率衰减抗性
    recovery: int = 50      # 恢复：W′ 回充速度
    positioning: int = 50   # 占位：集团中的跟车质量、分裂时的站位
    resilience: int = 50    # 意志：储备见底后的功率保持

    def clamped(self) -> "Attributes":
        def c(v: int) -> int:
            return max(1, min(100, int(v)))
        return Attributes(
            c(self.flat), c(self.climbing), c(self.sprint), c(self.time_trial),
            c(self.descending), c(self.endurance), c(self.recovery),
            c(self.positioning), c(self.resilience),
        )


@dataclass
class PhysioParams:
    """由属性推导出的物理参数。引擎只读这一层。"""

    cp: float                # 平路临界功率 W（EnergyState 的基准）
    cp_climb: float          # 陡坡上的临界功率 W
    w_prime: float           # 无氧储备 J
    peak_anaerobic: float    # 满储备时高于 CP 的最大瞬时功率 W
    cda_hoods: float         # 常规骑行姿势迎风面积 m^2
    cda_aero: float          # 气动姿势（TT / 下坡收身）
    crr: float
    total_mass: float        # 人 + 车 + 装备
    max_descent_speed: float # m/s
    recovery_mult: float
    durability: float
    draft_skill: float       # 0.85-1.15，跟车省力的效率修正
    grit: float              # 储备见底后能维持的 CP 比例


def derive_params(attr: Attributes, body_mass_kg: float) -> PhysioParams:
    """属性 -> 物理参数。所有魔法数字都在这里，方便统一调平衡。"""

    a = attr.clamped()

    # --- 有氧引擎 ---
    # 平路和陡坡各有一条 CP：平路看 flat，陡坡看 climbing，中间按坡度插值。
    # 这样"爬坡属性"对策划和玩家都有直白的含义——它就是你在坡上的持续功率，
    # 而不是一个说不清作用在哪的隐藏加成。
    # 3.4 W/kg（勉强完赛的工兵）到约 6.0 W/kg（大环赛争冠水平）
    cp = (3.40 + 0.0200 * a.flat + 0.0060 * a.endurance) * body_mass_kg
    cp_climb = (3.40 + 0.0200 * a.climbing + 0.0060 * a.endurance) * body_mass_kg

    # --- 无氧储备 ---
    # 10-27 kJ。纯冲刺手储备大但有氧一般，爬坡手反之，这个差异靠属性自然产生
    w_prime = (10.0 + 0.140 * a.sprint + 0.030 * a.endurance) * 1000.0

    # --- 冲刺峰值 ---
    # 高于 CP 的部分。sprint=100 的 72kg 车手可达 CP + ~1500W
    peak_anaerobic = (7.5 + 0.135 * a.sprint) * body_mass_kg

    # --- 气动 ---
    # 迎风面积随体型缩放（按 2/3 次方，面积 vs 体积）
    size_scale = (body_mass_kg / 70.0) ** (2.0 / 3.0)
    cda_hoods = 0.325 * size_scale * (1.0 - 0.0009 * a.time_trial)
    cda_aero = 0.255 * size_scale * (1.0 - 0.0016 * a.time_trial)

    return PhysioParams(
        cp=cp,
        cp_climb=cp_climb,
        w_prime=w_prime,
        peak_anaerobic=peak_anaerobic,
        cda_hoods=cda_hoods,
        cda_aero=cda_aero,
        crr=0.0042 - 0.0000015 * a.positioning,
        total_mass=body_mass_kg + BIKE_MASS + KIT_MASS,
        max_descent_speed=15.5 + 0.115 * a.descending,
        recovery_mult=0.55 + 0.011 * a.recovery,
        durability=0.55 + 0.011 * a.endurance,
        draft_skill=0.88 + 0.0026 * a.positioning,
        grit=0.80 + 0.0018 * a.resilience,
    )


@dataclass
class Rider:
    """一名车手的静态定义。比赛中的可变状态见 race.RiderState。"""

    rider_id: str
    name: str
    team_id: str
    body_mass_kg: float
    attributes: Attributes
    role: Role = Role.DOMESTIQUE
    form: float = 1.0      # 竞技状态 0.9-1.1，训练与疲劳系统的出口
    morale: float = 1.0    # 士气 0.9-1.1，影响愿不愿意苦战

    params: PhysioParams = field(init=False)

    def __post_init__(self) -> None:
        self.params = derive_params(self.attributes, self.body_mass_kg)
        # form 只缩放有氧与储备，不改气动和体重——状态好不会让你变瘦
        self.params.cp *= self.form
        self.params.cp_climb *= self.form
        self.params.w_prime *= 0.5 + 0.5 * self.form

    @property
    def climb_ratio(self) -> float:
        """陡坡 CP 相对平路 CP 的倍率。爬坡手 > 1，冲刺手 < 1。"""
        return self.params.cp_climb / self.params.cp

    def terrain_cp_mult(self, grade: float) -> float:
        """按坡度在平路能力与爬坡能力之间插值。6% 以上完全按爬坡能力算。"""
        if grade <= 0.005:
            return 1.0
        blend = min(1.0, (grade - 0.005) / 0.055)
        return 1.0 + (self.climb_ratio - 1.0) * blend

    def new_energy_state(self) -> EnergyState:
        p = self.params
        return EnergyState(
            cp=p.cp,
            w_prime=p.w_prime,
            recovery_mult=p.recovery_mult,
            durability=p.durability,
        )

    @property
    def cp_per_kg(self) -> float:
        return self.params.cp / self.body_mass_kg

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<Rider {self.name} {self.team_id} {self.cp_per_kg:.2f}W/kg>"
