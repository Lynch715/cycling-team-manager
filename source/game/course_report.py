"""赛道体检：不跑完整比赛，就说清楚这条赛道会产出什么样的比赛。

设计一条赛道最难受的地方，是改完之后不知道自己改了什么。跑一场完整
模拟要二十几秒，跑十遍才能看出趋势——没人会这么迭代。

这份报告用快速结算的那套物理结论，几毫秒给出五个判断：跑多快、
有多难、把人筛得多狠、利好哪类车手、什么时候会打起来。它不替代真跑一场，
但它让「改一个数字 → 看结果」这个循环从半分钟缩短到瞬间。

**每一项都要能直接指导修改。** 一份只报「难度 7.2 分」的报告是没用的，
作者不知道该动哪儿。所以每一项都附一句「想改的话动什么」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from game.quickresolve import GAP_SHAPE, climb_share, winner_speed_kmh
from sim.course import Course, Surface


@dataclass
class Verdict:
    label: str
    value: str
    note: str
    knob: str = ""


@dataclass
class Report:
    name: str
    length_km: float
    ascent_m: float
    verdicts: list[Verdict] = field(default_factory=list)
    profile_notes: list[str] = field(default_factory=list)
    favours: list[tuple[str, float]] = field(default_factory=list)


# 各角色在「爬坡占比」这个轴上的适应度。
# 这张表就是「这条赛道利好谁」的全部依据——它和 quickresolve 的
# stage_merit 是同一套逻辑的简化版，所以报告不会和实跑结果打架。
ROLE_CURVE = {
    "总成绩核心": lambda b: 0.55 + 0.45 * b,
    "爬坡手":     lambda b: 0.20 + 0.85 * b,
    "冲刺手":     lambda b: 0.95 - 0.85 * b,
    "领骑员":     lambda b: 0.80 - 0.70 * b,
    "平路手":     lambda b: 0.85 - 0.35 * b,
    "突围手":     lambda b: 0.50 + 0.10 * b,
    "工兵":       lambda b: 0.35,
}


# 决胜段的多个观察窗口，以及各自的权重。
# 单一窗口是不够的：于伊之墙只有 1.3 公里，摊进 25 公里的窗口里会被稀释成
# 「平坦」；而波焦顶上还有 5 公里下坡，用 2 公里窗口又会漏掉它。
# 越靠近终点的爬坡越决定胜负——权重就是这个意思。
FINALE_WINDOWS = [(2.0, 1.00), (5.0, 0.88), (12.0, 0.68), (25.0, 0.52)]


def finale_share(course: Course) -> float:
    """决胜段难度 0-1。

    这是全份报告里最重要的一个数——**决定胜负的不是总爬升，而是爬升在哪儿**。
    阿登之墙全程只有 4.8 m/km，按全程平均算是「平坦、利好冲刺手」；
    但它的终点是 19% 的于伊之墙，真实结果是冲刺手一个都活不下来。
    只看全程平均的报告会把作者引到完全相反的方向去。
    """
    best = 0.0
    for tail_km, weight in FINALE_WINDOWS:
        tail_m = min(tail_km * 1000, course.length_m)
        start = course.length_m - tail_m
        cursor = asc = 0.0
        for s in course.segments:
            seg_start, seg_end = cursor, cursor + s.length_m
            cursor = seg_end
            if seg_end <= start:
                continue
            overlap = seg_end - max(seg_start, start)
            if s.grade > 0:
                asc += overlap * math.sin(math.atan(s.grade))
        apk = asc / max(1.0, tail_m / 1000)
        best = max(best, min(0.90, max(0.0, (apk - 2.0) / 26.0)) * weight)
    return best


def analyse(course: Course, stage_type: str | None = None) -> Report:
    st = stage_type or course.stage_type.value
    km = course.length_m / 1000
    asc = course.total_ascent_m
    apk = asc / max(1.0, km)
    share = climb_share(course)

    r = Report(course.name, round(km, 1), round(asc))

    # --- 速度 ---
    v = winner_speed_kmh(course, st)
    hours = km / max(1.0, v)
    r.verdicts.append(Verdict(
        "预计冠军均速", f"{v:.1f} km/h",
        f"用时约 {int(hours)} 小时 {int(hours % 1 * 60)} 分",
        "改总长度或累计爬升"))

    # --- 难度 ---
    r.verdicts.append(Verdict(
        "每公里爬升", f"{apk:.1f} m/km",
        _difficulty_note(apk), "增删爬坡段或改坡度"))

    # --- 选拔性 ---
    shape = GAP_SHAPE.get(st, GAP_SHAPE["hilly"])
    drop = shape["drop"]
    if drop >= 0.20:
        sel, note = "很弱", "绝大多数人会一起过线，名次由终点冲刺决定"
    elif drop >= 0.12:
        sel, note = "中等", "会分成几个集团，但主集团仍然很大"
    elif drop > 0.0:
        sel, note = "强", "集团会被撕开，只有状态好的人能留在前面"
    else:
        sel, note = "极强", "没有集团保护，每个人按自己的能力各走各的"
    r.verdicts.append(Verdict("选拔性", sel, note, "改赛段类型，或调整爬坡占比"))

    # --- 关键路段 ---
    hard = _key_sections(course)
    if hard:
        r.verdicts.append(Verdict(
            "决胜点", f"{len(hard)} 处",
            "、".join(f"{n}（剩 {rem:.0f}km，{g * 100:.1f}%）"
                      for n, rem, g in hard[:3]),
            "把陡段往终点挪会让比赛更晚才分胜负"))

    # --- 路面与风 ---
    cob = sum(s.length_m for s in course.segments
              if s.surface is Surface.COBBLES) / 1000
    if cob > 0.5:
        r.verdicts.append(Verdict(
            "石板路", f"{cob:.1f} km（{cob / km:.0%}）",
            "摔车与机械故障风险大幅上升，利好体重大、抗颠簸的车手",
            "增删 surface=石板 的路段"))
    wind = max((s.crosswind for s in course.segments), default=0.0)
    if wind >= 3.0:
        r.verdicts.append(Verdict(
            "最强侧风", f"{wind:.1f} m/s",
            "会把集团吹成扇形，排在后面的人直接暴露在风里",
            "调 crosswind_ms"))

    # --- 决胜段 ---
    fin = finale_share(course)
    r.verdicts.append(Verdict(
        "决胜段难度", f"{fin:.0%}",
        _finale_note(fin, share), "把爬坡段往终点挪，或调整最后 25 公里的坡度"))

    # --- 利好谁 ---
    # 取「全程」和「决胜段」里更狠的那个：一条平路加一堵终点墙，
    # 是墙决定谁赢，不是那 200 公里平路。
    eff = max(share, fin * 0.92)
    r.favours = sorted(((k, f(eff)) for k, f in ROLE_CURVE.items()),
                       key=lambda x: -x[1])

    r.profile_notes = _profile_notes(course, km)
    return r


def _finale_note(fin: float, whole: float) -> str:
    if fin > whole + 0.25:
        return "前面再平，胜负也在最后这一段决出——冲刺手活不到终点"
    if fin > 0.55:
        return "结尾极难，只有爬坡能力最好的人能争胜"
    if fin > 0.30:
        return "结尾有分量，纯冲刺手会掉，但全能手还能跟"
    if fin < 0.08 and whole > 0.35:
        return "前面很难但结尾平缓：掉队的人有机会追回来，容易变成小集团冲刺"
    return "结尾平缓，大概率集团冲刺"


def _difficulty_note(apk: float) -> str:
    if apk < 5:
        return "平坦。冲刺手的主场，除非有侧风或石板路"
    if apk < 10:
        return "起伏。突围有机会，冲刺手大多能跟到最后"
    if apk < 16:
        return "丘陵。纯冲刺手会掉，爬坡型的人开始占优"
    if apk < 24:
        return "山地。总成绩会拉开差距"
    return "高山。真实的大环赛决胜赛段就在这个区间"


def _key_sections(course: Course) -> list[tuple[str, float, float]]:
    """找出坡度最陡的几段，以及它们离终点还有多远。"""
    out = []
    cursor = 0.0
    for s in course.segments:
        cursor += s.length_m
        if s.grade >= 0.055 and s.length_m >= 1200:
            out.append((s.name or "无名爬坡",
                        (course.length_m - cursor) / 1000, s.grade))
    out.sort(key=lambda x: -x[2])
    return out


def _profile_notes(course: Course, km: float) -> list[str]:
    """给作者的直白提醒。都是真实赛道设计里反复出现的问题。"""
    notes = []
    segs = course.segments
    if not segs:
        return notes

    tail = [s for s in segs[-3:]]
    if all(abs(s.grade) < 0.02 for s in tail):
        notes.append("终点前是平路：无论中途多难，最后大概率变成集团冲刺。"
                     "想避免的话，把一个爬坡段挪到最后 5 公里以内。")

    if segs[-1].grade > 0.05:
        notes.append("山顶终点：差距会一直拉到最后一米，"
                     "这是最能体现总成绩能力的收尾方式。")

    first_third = sum(s.length_m for s in segs[:max(1, len(segs) // 3)])
    hard_early = any(s.grade > 0.06 for s in segs[:max(1, len(segs) // 3)])
    if hard_early and first_third < course.length_m * 0.4:
        notes.append("开场就有陡坡：突围很难形成，"
                     "因为前 15 分钟本来就是全场最快的时候。")

    descents = [s for s in segs if s.grade < -0.04 and s.technical > 0.45]
    if descents:
        notes.append(f"有 {len(descents)} 段技术性下坡："
                     "下坡属性差的车手会掉时间，摔车风险也集中在这里。")

    if course.length_m / 1000 > 230:
        notes.append("超过 230 公里：后段的耐力衰减会成为主导因素，"
                     "利好耐力属性高的老将。")
    return notes


# --------------------------------------------------------------------------
# 文本输出
# --------------------------------------------------------------------------

def render(report: Report) -> str:
    lines = [f"{report.name}　{report.length_km} km　爬升 {report.ascent_m:.0f} m",
             "─" * 68]
    for v in report.verdicts:
        lines.append(f"  {v.label:<12}{v.value:<16}{v.note}")
        if v.knob:
            lines.append(f"  {'':<12}{'':<16}↳ 想改：{v.knob}")
    lines.append("")
    lines.append("  利好程度：" + "　".join(
        f"{k} {'█' * max(1, round(s * 8))}{s:.0%}" for k, s in report.favours[:4]))
    if report.profile_notes:
        lines.append("")
        for n in report.profile_notes:
            lines.append(f"  · {n}")
    return "\n".join(lines)
