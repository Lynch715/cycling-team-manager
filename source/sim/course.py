"""赛道：分段剖面、路面、风。

赛道用一串 Segment 描述，每段有长度、坡度、路面、风况。
海拔剖面与俯视地图都由这份数据程序化绘制（美术清单里明确不出图），
所以这里同时是"仿真输入"和"渲染数据源"。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum


class Surface(str, Enum):
    ASPHALT = "asphalt"
    COBBLES = "cobbles"
    GRAVEL = "gravel"
    WET = "wet"


SURFACE_CRR = {
    Surface.ASPHALT: 0.0,      # 增量，叠加在车手基础 crr 上
    Surface.COBBLES: 0.0055,
    Surface.GRAVEL: 0.0090,
    Surface.WET: 0.0012,
}

# 恶劣路面额外消耗体力（颠簸、控车），表现为 CP 的等效折扣
SURFACE_STRAIN = {
    Surface.ASPHALT: 1.0,
    Surface.COBBLES: 1.12,
    Surface.GRAVEL: 1.09,
    Surface.WET: 1.02,
}


class StageType(str, Enum):
    FLAT = "flat"
    HILLY = "hilly"
    MOUNTAIN = "mountain"
    SUMMIT_FINISH = "summit_finish"
    ITT = "itt"
    TTT = "ttt"
    COBBLED = "cobbled"
    CIRCUIT = "circuit"


@dataclass
class Segment:
    """一段等坡度赛道。"""

    length_m: float
    grade: float                      # tan 坡角，0.075 = 7.5%
    surface: Surface = Surface.ASPHALT
    headwind: float = 0.0             # m/s，正为逆风
    crosswind: float = 0.0            # m/s，侧风，触发分裂（echelon）
    technical: float = 0.0            # 0-1，弯道密度，影响下坡实际速度
    name: str = ""


@dataclass
class KomPoint:
    """爬坡积分点 / 冲刺点。"""

    distance_m: float
    label: str
    category: str = "cat3"   # hc / cat1..cat4 / sprint
    points: tuple[int, ...] = ()


@dataclass
class Course:
    """一条完整赛道。"""

    name: str
    segments: list[Segment]
    stage_type: StageType = StageType.FLAT
    start_altitude_m: float = 100.0
    koms: list[KomPoint] = field(default_factory=list)

    _cum: list[float] = field(init=False, repr=False)
    _alt: list[float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        self._cum = [0.0]
        self._alt = [self.start_altitude_m]
        for seg in self.segments:
            self._cum.append(self._cum[-1] + seg.length_m)
            rise = seg.length_m * math.sin(math.atan(seg.grade))
            self._alt.append(self._alt[-1] + rise)

    # ---- 查询 ----------------------------------------------------------

    @property
    def length_m(self) -> float:
        return self._cum[-1]

    def segment_index_at(self, distance_m: float) -> int:
        d = min(max(distance_m, 0.0), self.length_m - 1e-6)
        return min(bisect_right(self._cum, d) - 1, len(self.segments) - 1)

    def segment_at(self, distance_m: float) -> Segment:
        return self.segments[self.segment_index_at(distance_m)]

    def altitude_at(self, distance_m: float) -> float:
        i = self.segment_index_at(distance_m)
        seg = self.segments[i]
        into = distance_m - self._cum[i]
        return self._alt[i] + into * math.sin(math.atan(seg.grade))

    def grade_at(self, distance_m: float) -> float:
        return self.segment_at(distance_m).grade

    def remaining_m(self, distance_m: float) -> float:
        return max(0.0, self.length_m - distance_m)

    @property
    def total_ascent_m(self) -> float:
        return sum(
            seg.length_m * math.sin(math.atan(seg.grade))
            for seg in self.segments if seg.grade > 0
        )

    def elevation_profile(self, samples: int = 400) -> list[tuple[float, float]]:
        """给 UI 画海拔剖面用的采样点 [(距离m, 海拔m)]。"""
        step = self.length_m / samples
        return [(i * step, self.altitude_at(i * step)) for i in range(samples + 1)]

    def difficulty_ahead(self, distance_m: float, window_m: float = 5000.0) -> float:
        """未来一段距离的平均坡度，战术 AI 用来判断"该不该现在发力"。"""
        end = min(distance_m + window_m, self.length_m)
        if end <= distance_m:
            return 0.0
        total, d = 0.0, distance_m
        while d < end:
            i = self.segment_index_at(d)
            seg_end = min(self._cum[i] + self.segments[i].length_m, end)
            span = seg_end - d
            total += self.segments[i].grade * span
            d = seg_end + 1e-6
        return total / (end - distance_m)


# --------------------------------------------------------------------------
# 赛道构造工具
# --------------------------------------------------------------------------

def flat_stage(name: str = "平路赛段", length_km: float = 180.0) -> Course:
    """典型平路冲刺赛段：起伏很小，末段一个小坡再下坡进城。"""
    segs = [
        Segment(length_km * 1000 * 0.35, 0.002, name="出发平路"),
        Segment(length_km * 1000 * 0.12, 0.018, name="中段起伏"),
        Segment(length_km * 1000 * 0.10, -0.015, name="下坡"),
        Segment(length_km * 1000 * 0.28, 0.001, headwind=1.5, name="逆风平路"),
        Segment(length_km * 1000 * 0.10, 0.012, name="终点前小坡"),
        Segment(length_km * 1000 * 0.05, -0.004, technical=0.4, name="冲刺直道"),
    ]
    return Course(name, segs, StageType.FLAT, start_altitude_m=60)


def mountain_stage(name: str = "山地赛段", length_km: float = 165.0) -> Course:
    """两座大山 + 山顶终点的经典大环赛山地段。"""
    m = length_km * 1000
    segs = [
        Segment(m * 0.24, 0.004, name="山谷接近"),
        Segment(m * 0.11, 0.068, name="一类爬坡"),
        Segment(m * 0.08, -0.072, technical=0.6, name="技术下坡"),
        Segment(m * 0.17, 0.006, name="谷底过渡"),
        Segment(m * 0.09, 0.055, name="二类爬坡"),
        Segment(m * 0.07, -0.058, technical=0.4, name="下坡"),
        Segment(m * 0.11, 0.010, headwind=0.8, name="山脚接近"),
        Segment(m * 0.09, 0.081, name="终点爬坡下半"),
        Segment(m * 0.04, 0.095, name="终点爬坡陡段"),
    ]
    c = Course(name, segs, StageType.SUMMIT_FINISH, start_altitude_m=420)
    c.koms = [
        KomPoint(m * 0.35, "一类爬坡", "cat1", (10, 8, 6, 4, 2, 1)),
        KomPoint(m * 0.65, "二类爬坡", "cat2", (5, 3, 2, 1)),
        KomPoint(m * 1.00, "山顶终点", "hc", (20, 15, 12, 10, 8, 6, 4, 2)),
    ]
    return c


def itt_stage(name: str = "个人计时赛", length_km: float = 38.0) -> Course:
    m = length_km * 1000
    segs = [
        Segment(m * 0.40, 0.003, headwind=2.0, name="平路逆风段"),
        Segment(m * 0.15, 0.042, name="中段爬坡"),
        Segment(m * 0.13, -0.040, technical=0.3, name="下坡"),
        Segment(m * 0.32, 0.001, headwind=-1.5, name="顺风冲线段"),
    ]
    return Course(name, segs, StageType.ITT, start_altitude_m=80)


def cobbled_stage(name: str = "石板路赛段", length_km: float = 195.0) -> Course:
    """北方古典赛：平路为主，插入多段石板路。"""
    m = length_km * 1000
    segs: list[Segment] = [Segment(m * 0.30, 0.002, name="接近段")]
    for i in range(14):
        segs.append(Segment(2900, 0.012, Surface.COBBLES, crosswind=3.0,
                            technical=0.55, name=f"石板路 {i + 1}"))
        segs.append(Segment(m * 0.036, 0.001, crosswind=4.5, name=f"过渡 {i + 1}"))
    segs.append(Segment(max(1500.0, m - sum(s.length_m for s in segs)), 0.0,
                        technical=0.3, name="终点直道"))
    return Course(name, segs, StageType.COBBLED, start_altitude_m=25)
