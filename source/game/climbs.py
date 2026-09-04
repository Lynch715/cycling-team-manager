"""真实爬坡库：把公开的地理数据接进赛道系统。

**法务边界写在最前面。** 真实赛事名称（环法、巴黎-鲁贝）是注册商标，
真实车手姓名涉及人格权——游戏里一律用虚构的。但山口的名字和海拔剖面
属于公开地理事实，任何人都可以描述阿尔普迪埃兹有 21 个发夹弯、
平均坡度 8.1%。所以这里录的是**山**，拼出来的**赛事名是虚构的**。

**为什么要分段而不是只记平均坡度。** 阿尔普迪埃兹平均 8.1%，但起手第一
公里是 10.6%，集团正是在那里第一次被撕开；旺图山前 6 公里几乎是平的，
真正的战斗在圣埃斯泰弗弯之后才开始。只记平均值的话，引擎里所有的山
会变成同一座山——爬得快的人赢，没有别的故事。分段之后，「什么时候动手」
才重新变成一个问题。

这也是真实地理相对程序生成的全部价值：**不规则性**。程序生成的山坡度
平滑、节奏均匀；真实的山有喘息处、有假顶、有最后一公里突然立起来。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sim.course import KomPoint, Segment, Surface

ROOT = Path(__file__).resolve().parents[2]
CLIMBS_PATH = ROOT / "data" / "climbs.json"

_CACHE: dict | None = None

SURFACE_MAP = {"柏油": Surface.ASPHALT, "石板": Surface.COBBLES,
               "砾石": Surface.GRAVEL, "湿滑": Surface.WET}


def _data() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(CLIMBS_PATH.read_text(encoding="utf-8"))
    return _CACHE


@dataclass
class Climb:
    key: str
    name: str
    region: str
    top_m: float
    segments: list[Segment]
    note: str = ""

    @property
    def length_km(self) -> float:
        return sum(s.length_m for s in self.segments) / 1000

    @property
    def avg_grade(self) -> float:
        total = sum(s.length_m for s in self.segments)
        if total <= 0:
            return 0.0
        return sum(s.grade * s.length_m for s in self.segments) / total

    @property
    def max_grade(self) -> float:
        return max((s.grade for s in self.segments), default=0.0)

    @property
    def ascent_m(self) -> float:
        import math
        return sum(s.length_m * math.sin(math.atan(s.grade))
                   for s in self.segments if s.grade > 0)

    def category(self) -> str:
        """按爬升高度与平均坡度定级，规则贴近现实的分类习惯。"""
        # 阈值对着现实的分级校准过：加利比耶、阿尔普迪埃兹、安格利鲁
        # 在真实赛事里都是超级组，第一版把它们评成了一类。
        score = self.ascent_m * (1 + self.avg_grade * 6)
        if score > 1600:
            return "hc"
        if score > 900:
            return "cat1"
        if score > 450:
            return "cat2"
        if score > 200:
            return "cat3"
        return "cat4"

    def summary(self) -> str:
        return (f"{self.name}　{self.length_km:.1f} km @ "
                f"{self.avg_grade * 100:.1f}%（最陡 {self.max_grade * 100:.1f}%）"
                f"　爬升 {self.ascent_m:.0f} m　{self.category().upper()}")


def load_climb(key: str) -> Climb:
    raw = _data()["climbs"][key]
    default_surface = SURFACE_MAP.get(raw.get("surface", "柏油"), Surface.ASPHALT)
    segs = []
    for i, s in enumerate(raw["segments"], start=1):
        segs.append(Segment(
            length_m=float(s["length_km"]) * 1000,
            grade=float(s["grade_pct"]) / 100.0,
            surface=SURFACE_MAP.get(s.get("surface"), default_surface),
            crosswind=float(s.get("crosswind_ms", 0.0)),
            technical=float(s.get("technical", 0.0)),
            name=s.get("name") or f"{raw['name']} {i}",
        ))
    return Climb(key, raw["name"], raw.get("region", ""),
                 float(raw.get("top_m", 0)), segs, raw.get("note", ""))


def all_climbs() -> list[Climb]:
    return [load_climb(k) for k in _data()["climbs"]]


def cobble_sector(key: str) -> Segment:
    raw = _data()["cobble_sectors"][key]
    return Segment(
        length_m=float(raw["length_km"]) * 1000,
        grade=float(raw.get("grade_pct", 0.0)) / 100.0,
        surface=Surface.COBBLES,
        crosswind=float(raw.get("crosswind_ms", 2.5)),
        technical=float(raw.get("technical", 0.6)),
        name=f"{raw['name']}（{raw.get('stars', 3)}★）",
    )


def all_sectors() -> list[str]:
    return [k for k in _data()["cobble_sectors"] if not k.startswith("_")]


# --------------------------------------------------------------------------
# 拼装
# --------------------------------------------------------------------------

def descent_after(climb: Climb, length_km: float | None = None,
                  technical: float = 0.55) -> list[Segment]:
    """给一座山配一段下坡。

    下坡长度默认取爬坡的 1.1 倍、坡度略缓——真实的山两侧很少对称，
    而且下坡那一侧通常更长更缓，这也是为什么下坡技术在真实比赛里
    值钱：你有更长的时间可以追回来，或者摔掉。
    """
    km = length_km or climb.length_km * 1.1
    return [Segment(km * 1000, -climb.avg_grade * 0.85,
                    technical=technical, name=f"{climb.name} 下坡")]


def flat_link(length_km: float, name: str, headwind: float = 0.8,
              crosswind: float = 1.0, pieces: int = 0) -> list[Segment]:
    """两座山之间的过渡。切成几小段，避免出现几十公里坡度完全一致的假路。"""
    n = pieces or max(1, min(5, int(length_km // 12) + 1))
    out = []
    for i in range(n):
        out.append(Segment(length_km * 1000 / n, 0.004 if i % 2 else -0.003,
                           headwind=headwind, crosswind=crosswind,
                           name=name if n == 1 else f"{name} · {i + 1}"))
    return out


def kom_for(climb: Climb, at_km: float) -> KomPoint:
    table = {
        "hc": (20, 15, 12, 10, 8, 6, 4, 2),
        "cat1": (10, 8, 6, 4, 2, 1),
        "cat2": (5, 3, 2, 1),
        "cat3": (2, 1),
        "cat4": (1,),
    }
    cat = climb.category()
    return KomPoint(at_km * 1000, climb.name, cat, table[cat])
