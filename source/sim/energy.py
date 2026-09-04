"""能量系统：临界功率（CP）与无氧储备（W′）。

游戏里"体力条"通常是一个单一数值，掉光就骑不动。真实生理学更接近
两个池子：一个可以无限持续的有氧水平（CP），和一个有限的、超过 CP
就消耗、低于 CP 就缓慢回充的无氧储备（W′，单位焦耳）。

这个双池模型能自然产生几个管理游戏最需要的行为：
  · 车手可以短时间爆发追上突围，但爆发次数有限
  · 反复变速（山区攻击战）比匀速消耗大得多
  · 跟在集团里"省下的瓦数"直接换算成终点前还剩多少子弹
  · 恢复属性高的车手能在一个赛段里多打一次仗

参考 Skiba 等人的 W′ balance 模型，做了游戏化简化。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def recovery_tau(power_deficit: float) -> float:
    """W′ 回充时间常数（秒）。功率低于 CP 越多，回充越快。

    Skiba 2012 的经验拟合。deficit=0 时约 862s，deficit=200W 时约 390s。
    """
    d = max(power_deficit, 1.0)
    return 546.0 * math.exp(-0.01 * d) + 316.0


@dataclass
class EnergyState:
    """单个车手在一场比赛中的能量状态。"""

    cp: float                 # 临界功率（瓦），可近似为长时间可持续功率
    w_prime: float            # 无氧储备总量（焦耳）
    recovery_mult: float = 1.0  # 恢复属性带来的回充速度倍率
    durability: float = 1.0     # 耐力属性带来的抗衰减系数（越大越抗衰）
    terrain_mult: float = 1.0   # 当前地形下的 CP 倍率，由 race 层每步写入

    w_bal: float = field(init=False)      # 当前剩余 W′
    kj_spent: float = field(init=False, default=0.0)  # 累计做功（千焦）
    time_above_cp: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.w_bal = self.w_prime

    # ---- 查询 ----------------------------------------------------------

    @property
    def w_fraction(self) -> float:
        """剩余无氧储备比例，0-1。UI 上的"火柴"条。"""
        return max(0.0, self.w_bal / self.w_prime) if self.w_prime > 0 else 0.0

    def effective_cp(self) -> float:
        """考虑全程累计消耗后的当前 CP。

        真实比赛里 4 小时后的"新鲜 CP"会掉 8-15%，这是决定
        谁能在最后一座山扛住的核心变量，也是耐力属性的主要出口。
        """
        fade = 0.000045 * self.kj_spent / max(self.durability, 0.3)
        return self.cp * self.terrain_mult * max(0.72, 1.0 - fade)

    def max_power(self, peak_anaerobic: float) -> float:
        """当前能输出的瞬时最大功率。

        储备满时能打出完整的冲刺峰值，储备见底时只剩有氧水平。
        用平方根让"最后一点储备"仍有可观爆发力——真实冲刺就是如此，
        线性衰减会让终点前的博弈变得寡淡。
        """
        return self.effective_cp() + peak_anaerobic * math.sqrt(max(0.0, self.w_fraction))

    def sustainable_for(self, seconds: float) -> float:
        """在给定时长内可持续的功率上限（CP + W′/t，经典双曲线模型）。"""
        if seconds <= 0:
            return self.max_power(0.0)
        return self.effective_cp() + self.w_bal / seconds

    # ---- 推进 ----------------------------------------------------------

    def update(self, power: float, dt: float) -> None:
        """按实际输出功率推进一个时间步。"""
        cp_now = self.effective_cp()
        if power > cp_now:
            self.w_bal -= (power - cp_now) * dt
            self.time_above_cp += dt
        else:
            tau = recovery_tau(cp_now - power) / max(self.recovery_mult, 0.2)
            deficit = self.w_prime - self.w_bal
            self.w_bal += deficit * (1.0 - math.exp(-dt / tau))
        self.w_bal = min(self.w_bal, self.w_prime)
        self.kj_spent += power * dt / 1000.0

    def is_cracked(self) -> bool:
        """是否"爆掉"——储备见底，只能以有氧水平苟着。"""
        return self.w_bal <= 0.0
