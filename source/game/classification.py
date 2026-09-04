"""分类与积分：总成绩、冲刺积分、爬坡积分、青年、车队。

对应五件领骑衫美术资源（leader-jersey-01..05）：
  01 总成绩 · 02 冲刺积分 · 03 爬坡积分 · 04 最佳新秀 · 05 世界冠军（非分类）

规则刻意贴近现实：总成绩看累计时间（同时间按名次先后），冲刺积分让
平路赛段的名次更值钱，爬坡积分让突围有意义，青年衫给 25 岁以下。
这些不是装饰——它们是让"我这支队到底该追求什么"变成一个真问题的机制。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

# 赛段名次积分。平路赛段给冲刺手更厚的回报，山地赛段更平均。
STAGE_POINTS = {
    "flat":          (50, 30, 20, 18, 16, 14, 12, 10, 8, 7, 6, 5, 4, 3, 2),
    "cobbled":       (50, 30, 20, 18, 16, 14, 12, 10, 8, 7, 6, 5, 4, 3, 2),
    "hilly":         (30, 25, 22, 19, 17, 15, 13, 11, 9, 7, 6, 5, 4, 3, 2),
    "mountain":      (20, 17, 15, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1),
    "summit_finish": (20, 17, 15, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1),
    "itt":           (20, 17, 15, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1),
    # 团体计时赛不发分数榜积分：赢的是一整支队，把 50 分发给八个人里
    # 「排在最前面那个」纯属抽签，发给全队又会把分数榜冲垮。
    # 现实里的团体计时赛同样不计入冲刺积分榜。
    "ttt":           (),
}

# 赛段冠亚季军的总成绩时间奖励（秒）
TIME_BONUS = (10, 6, 4)

# 赛事等级 -> 世界排名积分倍率
TIER_MULTIPLIER = {
    "大环赛": 4.0, "纪念碑": 3.0, "世巡赛": 2.0,
    "职业系列赛": 1.0, "国内赛": 0.5,
}

YOUTH_MAX_AGE = 25


@dataclass
class StageResult:
    """一个赛段的结果。"""

    stage_index: int
    stage_type: str
    order: list[str]                       # 车手 id，按名次
    times: dict[str, float]                # 车手 id -> 完赛用时（秒）
    dnf: list[str] = field(default_factory=list)
    kom_points: dict[str, int] = field(default_factory=dict)
    sprint_points: dict[str, int] = field(default_factory=dict)

    @property
    def winner(self) -> str | None:
        return self.order[0] if self.order else None


@dataclass
class Classification:
    """一场赛事（可能是多赛段）的累计分类。"""

    gc_time: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    gc_rank_sum: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    points: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    kom: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    stage_wins: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    abandoned: set[str] = field(default_factory=set)

    def apply(self, result: StageResult) -> None:
        table = STAGE_POINTS[result.stage_type]
        for place, rider_id in enumerate(result.order):
            self.gc_time[rider_id] += result.times[rider_id]
            self.gc_rank_sum[rider_id] += place + 1
            if place < len(table):
                self.points[rider_id] += table[place]
            # 奖励秒数只在公路赛段发放，两种计时赛都没有这个规则
            if place < len(TIME_BONUS) and result.stage_type not in ("itt", "ttt"):
                self.gc_time[rider_id] -= TIME_BONUS[place]
        for rid, pts in result.kom_points.items():
            self.kom[rid] += pts
        for rid, pts in result.sprint_points.items():
            self.points[rid] += pts
        if result.order:
            self.stage_wins[result.order[0]] += 1
        self.abandoned.update(result.dnf)

    # ---- 排名 ----------------------------------------------------------

    def gc_standings(self) -> list[tuple[str, float]]:
        """总成绩榜。同时间按各赛段名次之和排序——这就是现实里的拆分规则。"""
        rows = [(rid, t) for rid, t in self.gc_time.items()
                if rid not in self.abandoned]
        rows.sort(key=lambda x: (round(x[1], 3), self.gc_rank_sum[x[0]]))
        return rows

    def points_standings(self) -> list[tuple[str, int]]:
        return sorted(((r, p) for r, p in self.points.items()
                       if r not in self.abandoned), key=lambda x: -x[1])

    def kom_standings(self) -> list[tuple[str, int]]:
        return sorted(((r, p) for r, p in self.kom.items()
                       if r not in self.abandoned), key=lambda x: -x[1])

    def youth_standings(self, ages: dict[str, int]) -> list[tuple[str, float]]:
        return [(r, t) for r, t in self.gc_standings()
                if ages.get(r, 99) <= YOUTH_MAX_AGE]

    def team_standings(self, team_of: dict[str, str]) -> list[tuple[str, float]]:
        """车队总成绩：每队前三名车手的累计时间之和。"""
        by_team: dict[str, list[float]] = defaultdict(list)
        for rid, t in self.gc_standings():
            by_team[team_of[rid]].append(t)
        rows = [(tid, sum(sorted(ts)[:3])) for tid, ts in by_team.items()
                if len(ts) >= 3]
        rows.sort(key=lambda x: x[1])
        return rows

    def jerseys(self, ages: dict[str, int]) -> dict[str, str]:
        """当前四件领骑衫的归属。返回 {美术资源名: 车手 id}。"""
        out: dict[str, str] = {}
        gc = self.gc_standings()
        pts = self.points_standings()
        kom = self.kom_standings()
        youth = self.youth_standings(ages)
        if gc:
            out["leader-jersey-01"] = gc[0][0]
        if pts:
            out["leader-jersey-02"] = pts[0][0]
        if kom:
            out["leader-jersey-03"] = kom[0][0]
        if youth:
            out["leader-jersey-04"] = youth[0][0]
        return out


def award_climb_points(order: list[str], category: str,
                       table: tuple[int, ...]) -> dict[str, int]:
    """按通过顺序发放爬坡或冲刺积分。"""
    return {rid: table[i] for i, rid in enumerate(order[:len(table)])}


def world_ranking_points(place: int, tier: str, prestige: int,
                         is_gc: bool = True) -> int:
    """世界排名积分：名次 + 赛事等级 + 声望三者的函数。

    用幂律衰减而不是线性表，因为冠军和第十名的价值差距在现实中是数量级的。
    这一条直接决定了小队"要不要赌一次突围"的期望值。
    """
    if place < 1:
        return 0
    base = 500.0 / (place ** 1.15)
    # 赛段冠军只给总成绩积分的一小部分：一个大环赛赢八个赛段的冲刺手，
    # 拿到的分数不应该超过一个赢下总成绩的车手。世界排名衡量的是
    # "谁是最好的车手"，不是"谁举手次数多"。
    return int(base * TIER_MULTIPLIER.get(tier, 1.0) * (0.5 + prestige / 100.0)
               * (1.0 if is_gc else 0.12))
