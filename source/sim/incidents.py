"""摔车与机械故障。

这是引擎里唯一的"意外"来源，也是最容易做砸的一块。做砸的方式只有一种：
把它做成纯随机。玩家输掉一场比赛，如果原因是一个和他所有决定都无关的
骰子，他不会觉得可惜，只会觉得被耍了。

所以这里每一份风险都挂在**玩家能看见、也能影响的东西**上：

  · 弯道密度和路面 —— 写在赛道数据里，赛前就能看到
  · 天气 —— 赛前可见，会改变要不要押安全的轮胎
  · 速度 —— 下坡冲得越猛越危险，这让「下坡」属性同时是收益和风险
  · 集团位置 —— 埋在集团中间最省力，但也最容易被前面的连环摔波及。
    这是全游戏最漂亮的一个权衡：省力和安全是矛盾的
  · 疲劳 —— 储备见底的人操作会变形，这让「保存体力」有了第二层意义

后果以损失时间为主。真实公路赛里绝大多数摔车的代价是追不上集团，
而不是退赛——一上来就让人退赛，玩家会觉得系统在惩罚他。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .course import Surface

# 每秒基础风险。数值经 verify_incidents.py 标定到真实发生率：
# 一场普通公路赛约 3-6% 的车手会摔，8-12% 会遇到机械故障。
CRASH_BASE = 1.05e-6
MECH_BASE = 3.2e-6

# 路面的风险倍率
SURFACE_CRASH = {
    Surface.ASPHALT: 1.0, Surface.WET: 2.3,
    Surface.COBBLES: 6.5, Surface.GRAVEL: 3.4,
}
SURFACE_MECH = {
    Surface.ASPHALT: 1.0, Surface.WET: 1.4,
    # 石板路是器材的坟场。倍率给到 15 看着夸张，但要记得一场石板路赛里
    # 真正在石板上的只有五分之一的里程——摊到全程之后才是真实的量级。
    Surface.COBBLES: 15.0, Surface.GRAVEL: 6.0,
}


class IncidentKind(str, Enum):
    MECHANICAL = "机械故障"
    PUNCTURE = "爆胎"
    CRASH_MINOR = "轻微摔车"
    CRASH_MAJOR = "严重摔车"
    ABANDON = "摔车退赛"


@dataclass
class Incident:
    kind: IncidentKind
    at_distance: float
    lost_seconds: float
    w_prime_lost: float = 0.0
    ends_race: bool = False

    @property
    def label(self) -> str:
        return self.kind.value


def crash_hazard(*, speed: float, grade: float, surface: Surface,
                 technical: float, group_size: int, draft_rank: int,
                 descending: int, w_fraction: float, rain: float) -> float:
    """每秒摔车概率。"""
    h = CRASH_BASE
    h *= SURFACE_CRASH[surface] * (1.0 + rain * 1.3)

    # 速度：风险大致随速度平方上升
    h *= max(0.15, (speed / 12.0) ** 2)

    # 弯道密度只在下坡和高速时真正致命
    if grade < -0.02:
        h *= 1.0 + technical * 3.2
        # 下坡技术好的人在同样的弯里更稳
        h *= 1.7 - descending / 100.0 * 1.0
    else:
        h *= 1.0 + technical * 0.9

    # 集团位置：埋在中间最省力，也最容易被连环摔波及。
    # 这一条让「省力」和「安全」变成一对真实的矛盾。
    if group_size >= 12:
        depth = min(1.0, draft_rank / max(8.0, group_size * 0.6))
        h *= 0.55 + 1.35 * depth
    else:
        h *= 0.5                       # 小集团视野好，几乎不会连环摔

    # 疲劳：储备见底的人操作会变形
    h *= 1.0 + 0.85 * (1.0 - max(0.0, min(1.0, w_fraction)))
    return h


def mechanical_hazard(*, surface: Surface, speed: float, rain: float) -> float:
    """每秒机械故障（含爆胎）概率。"""
    return (MECH_BASE * SURFACE_MECH[surface] * (1.0 + rain * 0.5)
            * max(0.3, speed / 12.0))


def roll_crash(rng: random.Random, distance: float, remaining_frac: float,
               resilience: int) -> Incident:
    """摔了之后有多严重。

    绝大多数是"爬起来追集团"，少数伤到骨头。意志属性高的人更能忍着骑完。
    """
    r = rng.random()
    tough = resilience / 100.0
    if r < 0.72:
        return Incident(IncidentKind.CRASH_MINOR, distance,
                        lost_seconds=rng.uniform(20, 75),
                        w_prime_lost=rng.uniform(0.05, 0.18))
    if r < 0.955:
        return Incident(IncidentKind.CRASH_MAJOR, distance,
                        lost_seconds=rng.uniform(90, 280),
                        w_prime_lost=rng.uniform(0.25, 0.55))
    # 退赛：意志越强越可能挣扎着继续
    if rng.random() < 0.55 - tough * 0.30:
        return Incident(IncidentKind.ABANDON, distance,
                        lost_seconds=0.0, ends_race=True)
    return Incident(IncidentKind.CRASH_MAJOR, distance,
                    lost_seconds=rng.uniform(200, 420),
                    w_prime_lost=rng.uniform(0.4, 0.7))


def roll_mechanical(rng: random.Random, distance: float,
                    in_main_bunch: bool) -> Incident:
    """机械故障停多久。

    在主集团里，队车就在后面，换轮很快；跑在突围里或者已经掉队的人，
    队车够不着，要等中立器材车——这一条让「待在集团里」多了一个理由。
    """
    kind = IncidentKind.PUNCTURE if rng.random() < 0.7 else IncidentKind.MECHANICAL
    base = rng.uniform(18, 40) if kind is IncidentKind.PUNCTURE \
        else rng.uniform(35, 90)
    if not in_main_bunch:
        base *= rng.uniform(1.6, 2.6)
    return Incident(kind, distance, lost_seconds=base,
                    w_prime_lost=0.0)
