"""赛历的读写、校验与体检。

赛道数据化之后，剩下的最后一块写死的东西就是赛历——25 场赛事排在哪一天、
每场几个赛段、什么地形，全在 `generate_world.py` 的一张常量表里。玩家想
自己造一个大环赛、或者把七条真实赛道编成一个古典赛赛季，做不到。

这一层把它拆出来。规则和赛道一致：**文件存在就以文件为准**，没有才用
内置的默认赛历。

**赛历体检比单条赛道的体检更重要。** 单条赛道跑歪了只毁一场比赛；赛历
排歪了会毁掉整个赛季——比如全年 80% 是平路赛段，爬坡手一整年无事可做，
玩家的阵容里就永远不该有爬坡手。这类问题在单场比赛里完全看不出来。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CALENDAR_PATH = ROOT / "data" / "calendar.json"

TIERS = ["大环赛", "纪念碑", "世巡赛", "职业系列赛", "国内赛"]

# 赛段类型的字母缩写。赛历里用字母串描述一场赛事的赛段构成，
# 比如 "FHIFMSF" ——七个赛段：平路、丘陵、计时、平路、山地、山顶终点、平路。
# 用缩写而不是完整数组，是因为一个 21 赛段的大环赛写成数组会有 21 行，
# 而写成一行字母，整场比赛的节奏一眼就能看出来。
STAGE_LETTERS = {
    "F": "flat", "H": "hilly", "M": "mountain",
    "S": "summit_finish", "I": "itt", "C": "cobbled",
    "T": "ttt",          # 团体计时赛，常用作大环赛的开幕赛段
}
LETTER_OF = {v: k for k, v in STAGE_LETTERS.items()}

TERRAINS = ["alps", "pyrenees", "dutch", "italian_hills",
            "cobbles", "coast", "city", "desert"]

SEASON_DAYS = 300


@dataclass
class Issue:
    level: str
    where: str
    text: str


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def validate(d: dict) -> list[Issue]:
    issues: list[Issue] = []
    races = d.get("races") or []
    if not races:
        return [Issue("错误", "整体", "赛历里一场比赛都没有")]

    seen_ids: set[str] = set()
    for r in races:
        where = f"{r.get('id', '?')}「{r.get('name', '')}」"

        rid = r.get("id")
        if not rid:
            issues.append(Issue("错误", where, "缺少 id"))
        elif rid in seen_ids:
            issues.append(Issue("错误", where, f"id「{rid}」重复"))
        seen_ids.add(rid)

        if r.get("tier") not in TIERS:
            issues.append(Issue("错误", where,
                                f"未知等级「{r.get('tier')}」，"
                                f"应为 {'/'.join(TIERS)}"))

        day = r.get("start_day", 0)
        if not (1 <= day <= SEASON_DAYS):
            issues.append(Issue("错误", where,
                                f"起始日 {day} 超出 1–{SEASON_DAYS}"))

        recipe = r.get("stages", "")
        if not recipe:
            issues.append(Issue("错误", where, "没有赛段构成"))
        for ch in recipe:
            if ch not in STAGE_LETTERS:
                issues.append(Issue("错误", where,
                                    f"未知赛段字母「{ch}」，"
                                    f"可用 {'/'.join(STAGE_LETTERS)}"))
        if len(recipe) > 23:
            issues.append(Issue("警告", where,
                                f"{len(recipe)} 个赛段，比现实中最长的大环赛还长"))

        if r.get("terrain") not in TERRAINS:
            issues.append(Issue("错误", where, f"未知地形「{r.get('terrain')}」"))

        p = r.get("prestige", 0)
        if not (1 <= p <= 100):
            issues.append(Issue("错误", where, f"声望 {p} 应在 1–100"))

    # 时间冲突：两场比赛的日期区间重叠
    spans = sorted(((r.get("start_day", 0),
                     r.get("start_day", 0) + len(r.get("stages", "")) - 1, r)
                    for r in races), key=lambda x: x[0])
    for (s1, e1, r1), (s2, e2, r2) in zip(spans, spans[1:]):
        if s2 <= e1:
            lv = "错误" if (r1.get("tier") in TIERS[:2]
                            and r2.get("tier") in TIERS[:2]) else "警告"
            issues.append(Issue(
                lv, f"{r1.get('name')} / {r2.get('name')}",
                f"日期重叠（第 {s2}–{e1} 天）"
                + ("，两场都是顶级赛事，车队会被撕成两半"
                   if lv == "错误" else "，中小赛事撞车在现实中很常见")))

    return issues


# --------------------------------------------------------------------------
# 体检
# --------------------------------------------------------------------------

@dataclass
class CalendarReport:
    total_races: int
    total_days: int
    stage_mix: dict[str, int]
    verdicts: list[tuple[str, str, str]]     # (标题, 值, 说明)
    notes: list[str]


# 每类车手一年至少需要多少个「属于他」的比赛日，否则这个角色在阵容里
# 就是纯粹的浪费。数字按 107 个比赛日的赛季量级取。
ROLE_DEMAND = {
    "冲刺手": ("F", 30, "平路赛段"),
    "爬坡手": ("MS", 18, "山地与山顶终点"),
    "总成绩核心": ("MSI", 22, "山地与计时赛"),
    "平路手": ("FC", 32, "平路与石板路"),
}


def analyse(d: dict) -> CalendarReport:
    races = d.get("races") or []
    mix: dict[str, int] = {}
    total_days = 0
    for r in races:
        for ch in r.get("stages", ""):
            mix[ch] = mix.get(ch, 0) + 1
            total_days += 1

    verdicts: list[tuple[str, str, str]] = []
    notes: list[str] = []

    verdicts.append(("赛事总数", f"{len(races)} 场",
                     f"共 {total_days} 个比赛日"))

    # 地形配比
    parts = "、".join(f"{STAGE_LETTERS[k]} {v}" for k, v in
                      sorted(mix.items(), key=lambda x: -x[1]))
    verdicts.append(("赛段构成", parts, "决定了哪类车手全年有活干"))

    for role, (letters, need, label) in ROLE_DEMAND.items():
        got = sum(mix.get(ch, 0) for ch in letters)
        if got < need:
            notes.append(f"{role}全年只有 {got} 个{label}赛段（建议至少 {need} 个）"
                         f"——这个角色在阵容里会长期闲置，玩家不会想签他。")

    # 大赛之间的间隔
    big = sorted((r for r in races if r.get("tier") in ("大环赛", "纪念碑")),
                 key=lambda r: r.get("start_day", 0))
    if len(big) >= 2:
        gaps = []
        for a, b in zip(big, big[1:]):
            end_a = a.get("start_day", 0) + len(a.get("stages", "")) - 1
            gaps.append(b.get("start_day", 0) - end_a)
        worst = min(gaps)
        verdicts.append(("顶级赛事最小间隔", f"{worst} 天",
                         "少于 14 天的话，同一批核心车手无法两场都打"))
        if worst < 14:
            i = gaps.index(worst)
            notes.append(f"「{big[i].get('name')}」和「{big[i + 1].get('name')}」"
                         f"只隔 {worst} 天：车手不可能连打两场，"
                         f"其中一场会变成二线阵容的比赛。")

    # 赛季分布
    if races:
        days = [r.get("start_day", 0) for r in races]
        first, last = min(days), max(days)
        verdicts.append(("赛季跨度", f"第 {first} – {last} 天",
                         f"约 {(last - first) / 30:.1f} 个月"))
        halves = [sum(1 for x in days if x <= (first + last) / 2),
                  sum(1 for x in days if x > (first + last) / 2)]
        if abs(halves[0] - halves[1]) > len(races) * 0.35:
            notes.append(f"赛事分布不均：前半季 {halves[0]} 场、"
                         f"后半季 {halves[1]} 场。空档太长的那一半，"
                         f"玩家会觉得赛季在空转。")

    grand = [r for r in races if r.get("tier") == "大环赛"]
    if len(grand) < 2:
        notes.append("大环赛少于两场：赛季缺少高潮，而且「主攻哪个大环赛」"
                     "这个赛季规划决策会失去意义。")

    return CalendarReport(len(races), total_days, mix, verdicts, notes)


def render(rep: CalendarReport) -> str:
    lines = ["赛历体检", "─" * 68]
    for label, value, note in rep.verdicts:
        lines.append(f"  {label:<16}{value:<28}{note}")
    if rep.notes:
        lines.append("")
        for n in rep.notes:
            lines.append(f"  · {n}")
    else:
        lines.append("")
        lines.append("  · 没有发现问题。")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------

def save(races: list[dict], path: Path | None = None) -> Path:
    p = path or CALENDAR_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "_说明": [
            "赛历。stages 字段用字母描述赛段构成：",
            "F 平路 / H 丘陵 / M 山地 / S 山顶终点 / I 计时赛 / C 石板路",
            "例如 FHIFMSF = 七个赛段的平路-丘陵-计时-平路-山地-山顶-平路。",
            "",
            "改完跑 python3 source/calendar_check.py 体检，",
            "再跑 generate_world.py 让新赛历生效。",
        ],
        "races": races,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load(strict: bool = True) -> list[dict] | None:
    """读赛历。文件不存在返回 None，调用方回退到内置默认。"""
    if not CALENDAR_PATH.exists():
        return None
    d = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    errs = [i for i in validate(d) if i.level == "错误"]
    if errs and strict:
        lines = "\n".join(f"  · {i.where}：{i.text}" for i in errs[:8])
        raise ValueError(f"赛历有 {len(errs)} 处错误：\n{lines}")
    return d["races"]
