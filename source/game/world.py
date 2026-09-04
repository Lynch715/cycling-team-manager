"""世界数据模型：车手档案、车队、赞助商、赛事、赛季。

这一层是"游戏世界长什么样"，比赛引擎（sim 包）是"比赛怎么跑"。
两者刻意分开：引擎只认 sim.Rider（体重 + 九项属性），对合同、年龄、
士气一无所知。世界层负责把档案翻译成引擎能吃的东西。

好处是数值平衡和经营玩法可以各改各的，互不干扰。

**美术资源绑定**：每个实体都带一个 art_* 字段，直接指向 assets/art 下的
文件名（不含路径和扩展名）。这样 GPT 出的图一落盘就能挂上，不需要再写
一层映射表；前端拿到 JSON 就知道该加载哪张图。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from sim.rider import Attributes, Rider, Role


# --------------------------------------------------------------------------
# 赞助商
# --------------------------------------------------------------------------

class Industry(str, Enum):
    BANK = "金融"
    TELECOM = "通信"
    ENERGY = "能源"
    RETAIL = "零售"
    TECH = "科技"
    FOOD = "食品"
    AUTO = "汽车"
    INSURANCE = "保险"
    BUILDING = "建材"
    APPAREL = "服饰"


@dataclass
class Sponsor:
    sponsor_id: str
    name: str
    industry: Industry
    wealth: int             # 1-5，决定能出多少钱
    loyalty: int            # 1-5，成绩下滑时会不会立刻走人
    art_logo: str           # assets/art/07_sponsors/<art_logo>.png

    @property
    def annual_budget(self) -> int:
        """年度赞助额（万元）。财力每档翻一档，头部赞助商是小赞助商的十几倍。"""
        return int(180 * (1.85 ** (self.wealth - 1)))


# --------------------------------------------------------------------------
# 车队
# --------------------------------------------------------------------------

class Division(str, Enum):
    WORLD = "世巡赛"
    PRO = "职业队"
    CONTI = "洲际队"


@dataclass
class Team:
    team_id: str
    name: str
    short_name: str
    division: Division
    country: str
    prestige: int                      # 1-100，影响签人能力和赞助报价
    sponsor_ids: list[str]
    art_badge: str                     # 08_teams/<art_badge>.png
    jersey_pattern: int                # 1-10，队服花纹模板编号
    color_primary: str
    color_secondary: str
    color_accent: str
    budget: int = 0                    # 当季预算（万元），由赞助商推导
    rider_ids: list[str] = field(default_factory=list)
    points_history: list[int] = field(default_factory=list)  # 近三个赛季的积分


# --------------------------------------------------------------------------
# 车手档案
# --------------------------------------------------------------------------

@dataclass
class RiderProfile:
    rider_id: str
    name: str
    nation: str
    age: int
    body_mass_kg: float
    height_cm: int
    team_id: str
    role: Role
    attributes: Attributes
    potential: int                     # 属性成长上限的整体天花板 1-100
    art_portrait: str                  # 01_riders/portraits/<art_portrait>.png
    art_body: str                      # 01_riders/body_types/<art_body>.png
    salary: int = 0                    # 年薪（万元）
    contract_years: int = 1            # 剩余合同年数
    form: float = 1.0
    morale: float = 1.0
    fatigue: float = 0.0               # 0-1，赛季累积疲劳
    career_wins: int = 0
    target_races: list[str] = field(default_factory=list)  # 本季主攻的赛事 id
    training: dict = field(default_factory=dict)   # 训练方案，见 game.training

    def to_sim_rider(self) -> Rider:
        """翻译成比赛引擎认识的车手。

        疲劳在这里折算进 form：引擎不需要知道"疲劳"这个概念，
        它只关心这名车手今天的有氧引擎有多强。
        """
        effective_form = self.form * (1.0 - 0.25 * self.fatigue)
        return Rider(
            rider_id=self.rider_id, name=self.name, team_id=self.team_id,
            body_mass_kg=self.body_mass_kg, attributes=self.attributes,
            role=self.role, form=round(effective_form, 4), morale=self.morale,
        )

    @property
    def overall(self) -> int:
        """综合评分：按角色加权，用于列表排序和转会报价。

        不能简单取九项平均——那样冲刺手和爬坡手都会被评成平庸，
        而现实里专精一项到 95 的人远比样样 70 的人值钱。
        """
        a = self.attributes
        weights = {
            Role.LEADER: {"climbing": 3, "time_trial": 3, "endurance": 2,
                          "flat": 1, "resilience": 1, "recovery": 1},
            Role.CLIMBER: {"climbing": 4, "endurance": 2, "recovery": 1,
                           "resilience": 1, "descending": 1},
            Role.SPRINTER: {"sprint": 4, "flat": 2, "positioning": 2,
                            "recovery": 1},
            Role.LEADOUT: {"flat": 3, "positioning": 3, "sprint": 1,
                           "time_trial": 1},
            Role.ROULEUR: {"flat": 3, "time_trial": 2, "endurance": 2,
                           "resilience": 1},
            Role.BREAKAWAY: {"endurance": 3, "resilience": 2, "flat": 2,
                             "recovery": 2, "climbing": 1},
            Role.DOMESTIQUE: {"flat": 2, "endurance": 2, "recovery": 1,
                              "climbing": 1, "positioning": 1},
        }[self.role]
        total = sum(getattr(a, k) * w for k, w in weights.items())
        return int(round(total / sum(weights.values())))


# --------------------------------------------------------------------------
# 赛事
# --------------------------------------------------------------------------

class RaceTier(str, Enum):
    GRAND_TOUR = "大环赛"
    MONUMENT = "纪念碑"
    WORLD_TOUR = "世巡赛"
    PRO_SERIES = "职业系列赛"
    NATIONAL = "国内赛"


# 赛道地形原型 -> (卷轴主题, 地标美术)
TERRAIN_ART = {
    "alps": ("alpine", "01-alps"),
    "pyrenees": ("alpine", "02-pyrenees"),
    "dutch": ("plains", "03-dutch-plains"),
    "italian_hills": ("plains", "04-italian-hills"),
    "cobbles": ("plains", "05-cobblestones"),
    "coast": ("coast", "06-coast"),
    "city": ("city", "07-city"),
    "desert": ("coast", "08-desert"),
}


@dataclass
class StageSpec:
    """一个赛段的生成参数。真正的赛道由 sim.course 程序化生成。"""

    index: int
    name: str
    stage_type: str          # flat / hilly / mountain / summit_finish / itt / cobbled
    length_km: float
    terrain: str             # TERRAIN_ART 的键
    seed: int

    @property
    def art_parallax(self) -> str:
        return TERRAIN_ART[self.terrain][0]

    @property
    def art_landmark(self) -> str:
        return TERRAIN_ART[self.terrain][1]


@dataclass
class RaceEvent:
    race_id: str
    name: str
    tier: RaceTier
    country: str
    start_day: int                     # 赛季第几天（1-300）
    prestige: int                      # 1-100，影响积分与赞助商满意度
    stages: list[StageSpec]
    art_badge: str = ""                # 只有六大赛有专属徽章，其余留空用排版
    art_landmark: str = ""

    @property
    def is_stage_race(self) -> bool:
        return len(self.stages) > 1

    @property
    def days(self) -> int:
        return len(self.stages)


@dataclass
class World:
    season: int
    sponsors: list[Sponsor]
    teams: list[Team]
    riders: list[RiderProfile]
    calendar: list[RaceEvent]

    # ---- 索引 ----------------------------------------------------------

    def team(self, team_id: str) -> Team:
        return next(t for t in self.teams if t.team_id == team_id)

    def rider(self, rider_id: str) -> RiderProfile:
        return next(r for r in self.riders if r.rider_id == rider_id)

    def roster(self, team_id: str) -> list[RiderProfile]:
        return [r for r in self.riders if r.team_id == team_id]

    def sponsor(self, sponsor_id: str) -> Sponsor:
        return next(s for s in self.sponsors if s.sponsor_id == sponsor_id)

    # ---- 序列化 --------------------------------------------------------

    def to_dict(self) -> dict:
        def enc(obj):
            d = asdict(obj)
            for k, v in list(d.items()):
                if isinstance(v, Enum):
                    d[k] = v.value
            return d

        return {
            "season": self.season,
            "sponsors": [enc(s) for s in self.sponsors],
            "teams": [enc(t) for t in self.teams],
            "riders": [
                {**enc(r), "overall": r.overall,
                 "attributes": asdict(r.attributes)}
                for r in self.riders
            ],
            "calendar": [
                {**enc(e),
                 "stages": [{**asdict(s), "art_parallax": s.art_parallax,
                             "art_landmark": s.art_landmark} for s in e.stages]}
                for e in self.calendar
            ],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    @staticmethod
    def load(path: str | Path) -> "World":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        sponsors = [Sponsor(s["sponsor_id"], s["name"], Industry(s["industry"]),
                            s["wealth"], s["loyalty"], s["art_logo"])
                    for s in raw["sponsors"]]
        teams = [Team(t["team_id"], t["name"], t["short_name"],
                      Division(t["division"]), t["country"], t["prestige"],
                      t["sponsor_ids"], t["art_badge"], t["jersey_pattern"],
                      t["color_primary"], t["color_secondary"], t["color_accent"],
                      t["budget"], t["rider_ids"], t.get("points_history", []))
                 for t in raw["teams"]]
        riders = []
        for r in raw["riders"]:
            riders.append(RiderProfile(
                r["rider_id"], r["name"], r["nation"], r["age"],
                r["body_mass_kg"], r["height_cm"], r["team_id"], Role(r["role"]),
                Attributes(**r["attributes"]), r["potential"],
                r["art_portrait"], r["art_body"], r["salary"],
                r["contract_years"], r["form"], r["morale"], r["fatigue"],
                r["career_wins"], r.get("target_races", []),
                r.get("training", {})))
        calendar = []
        for e in raw["calendar"]:
            stages = [StageSpec(s["index"], s["name"], s["stage_type"],
                                s["length_km"], s["terrain"], s["seed"])
                      for s in e["stages"]]
            calendar.append(RaceEvent(e["race_id"], e["name"], RaceTier(e["tier"]),
                                      e["country"], e["start_day"], e["prestige"],
                                      stages, e["art_badge"], e["art_landmark"]))
        return World(raw["season"], sponsors, teams, riders, calendar)
