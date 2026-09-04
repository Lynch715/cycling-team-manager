"""《模拟自行车队经理》核心比赛模拟引擎。

技术无关：只依赖 Python 标准库，不涉及任何渲染或引擎 API。
Unity / Godot / Web 前端都可以把它当作一个纯数值服务来调用。

    from sim import build_peloton, mountain_stage, Race

    riders = build_peloton(n_teams=20)
    result = Race(mountain_stage(), riders, seed=42).run()
    print(result.winner.rider.name)
"""

from .course import (
    Course, KomPoint, Segment, StageType, Surface,
    cobbled_stage, flat_stage, itt_stage, mountain_stage,
)
from .energy import EnergyState
from .pack import Group, draft_factor, form_groups
from .race import Race, RaceResult, RiderState, format_gap, format_time
from .rider import Attributes, PhysioParams, Rider, Role, derive_params
from .roster import build_peloton, make_rider
from .tactics import Mode

__all__ = [
    "Attributes", "Course", "EnergyState", "Group", "KomPoint", "Mode",
    "PhysioParams", "Race", "RaceResult", "Rider", "RiderState", "Role",
    "Segment", "StageType", "Surface", "build_peloton", "cobbled_stage",
    "derive_params", "draft_factor", "flat_stage", "form_groups", "format_gap",
    "format_time", "itt_stage", "make_rider", "mountain_stage",
]
