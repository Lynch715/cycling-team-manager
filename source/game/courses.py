"""把赛段配置（StageSpec）变成引擎能跑的赛道（sim.Course）。

赛道是程序化生成的，不是手摆的。理由有三个：
25 条赛道 × 平均 4 个赛段手摆是几周的工作量；玩家在第二个赛季就会记住
每条赛道，随机化能延长新鲜度；俯视地图和海拔剖面本来就要程序化绘制
（美术清单里明确不出图），生成器和渲染器共用同一份数据最省事。

每种赛段类型有自己的"骨架"，随机的只是坡度、长度和位置的抖动。
所以同一个 seed 永远得到同一条赛道，而不同 seed 的山地赛段一定还是山地赛段。
"""

from __future__ import annotations

import random

from game.world import StageSpec
from sim.course import Course, KomPoint, Segment, StageType, Surface

# 爬坡等级 -> (KOM 积分, 典型坡度, 典型长度 km)
KOM_CATEGORIES = {
    "hc":   ((20, 15, 12, 10, 8, 6, 4, 2), 0.090, 16.0),
    "cat1": ((10, 8, 6, 4, 2, 1), 0.080, 12.0),
    "cat2": ((5, 3, 2, 1), 0.070, 8.0),
    "cat3": ((2, 1), 0.058, 5.0),
    "cat4": ((1,), 0.048, 3.0),
}

SPRINT_POINTS = (20, 17, 15, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1)

# 地形 -> (起始海拔, 路面, 逆风均值, 侧风均值)
TERRAIN_PROFILE = {
    "alps": (620, Surface.ASPHALT, 0.5, 0.5),
    "pyrenees": (480, Surface.ASPHALT, 0.8, 1.0),
    "dutch": (8, Surface.ASPHALT, 1.6, 4.5),
    "italian_hills": (180, Surface.ASPHALT, 0.6, 1.0),
    "cobbles": (30, Surface.COBBLES, 1.0, 3.5),
    "coast": (15, Surface.ASPHALT, 1.4, 3.0),
    "city": (60, Surface.ASPHALT, 0.8, 1.0),
    "desert": (320, Surface.ASPHALT, 2.0, 3.5),
}


def _climb(rng: random.Random, category: str, name: str) -> list[Segment]:
    """一座山：接近段 + 爬坡 + 下坡。"""
    _, grade, length_km = KOM_CATEGORIES[category]
    grade *= rng.uniform(0.88, 1.14)
    length = length_km * 1000 * rng.uniform(0.85, 1.18)
    # 陡段放在爬坡后半，符合真实山路"越往上越陡"的观感，也让攻击点更集中
    return [
        Segment(length * 0.6, grade * 0.9, name=f"{name} 下半段"),
        Segment(length * 0.4, grade * 1.18, name=f"{name} 陡段"),
        Segment(length * rng.uniform(0.7, 1.1), -grade * rng.uniform(0.85, 1.1),
                technical=rng.uniform(0.25, 0.7), name=f"{name} 下坡"),
    ]


def _filler(rng: random.Random, length_m: float, terrain: str,
            name: str) -> list[Segment]:
    """平缓的过渡路段。返回若干小段而不是一整段。

    现实里不存在「连续 48 公里坡度完全一致」的路。切成几小段之后，
    海拔剖面会有真实的起伏，风况也能沿途变化——而且集团会在这些起伏上
    自然地拉扯，而不是一路匀速滑行。

    这个问题是 course_check 的体检报告抓出来的：全部 107 条赛道里有 57 条
    带着几十公里长的单一路段。生成器自己不会发现这种事。
    """
    _, surface, headwind, crosswind = TERRAIN_PROFILE[terrain]
    length_m = max(300.0, length_m)
    n = max(1, min(6, int(length_m // 9000) + 1))
    base_hw = rng.gauss(headwind, 1.0)
    out = []
    for i in range(n):
        span = length_m / n * rng.uniform(0.82, 1.18)
        out.append(Segment(
            max(250.0, span),
            rng.uniform(-0.016, 0.022),
            surface=Surface.ASPHALT,
            headwind=base_hw + rng.gauss(0, 0.9),
            crosswind=max(0.0, rng.gauss(crosswind, 1.5)),
            name=name if n == 1 else f"{name} · {i + 1}",
        ))
    # 长度归一，避免抖动之后总长跑偏
    scale = length_m / sum(s.length_m for s in out)
    for s in out:
        s.length_m *= scale
    return out


def build_course(spec: StageSpec, name_prefix: str = "",
                 course_id: str | None = None) -> Course:
    """取一条赛道。

    **有数据文件就以文件为准**，没有才程序生成。这样作者手改过的赛道
    不会在下次生成时被悄悄覆盖，而没改过的赛道仍然自动有内容——
    程序生成从「唯一来源」降级成「给你一份初稿」。
    """
    if course_id:
        from game.course_io import course_path, load_course
        if course_path(course_id).exists():
            saved = load_course(course_id)
            # 但文件里的赛道类型必须和赛历对得上。赛历把某个赛段改成了
            # 团体计时赛，而硬盘上还存着它原来那条 200 公里的公路赛道——
            # 结果是团体计时赛的模型拿着一条公路赛道跑，三个小时也跑不完。
            # **「以文件为准」的前提是文件说的还是同一件事。**
            if saved.stage_type.value == spec.stage_type:
                return saved
    return generate_course(spec, name_prefix)


def generate_course(spec: StageSpec, name_prefix: str = "") -> Course:
    """程序生成一条赛道。相同 seed 必然得到相同结果。"""
    rng = random.Random(spec.seed)
    total = spec.length_km * 1000
    alt, surface, _, _ = TERRAIN_PROFILE[spec.terrain]
    segs: list[Segment] = []
    koms: list[KomPoint] = []
    name = f"{name_prefix}{spec.name}".strip()

    if spec.stage_type == "itt":
        segs = [*_filler(rng, total * 0.38, spec.terrain, "起步平路"),
                Segment(total * 0.16, rng.uniform(0.030, 0.052), name="中段爬坡"),
                Segment(total * 0.14, -rng.uniform(0.030, 0.050),
                        technical=rng.uniform(0.2, 0.5), name="下坡"),
                *_filler(rng, total * 0.32, spec.terrain, "冲线段")]
        return Course(name, segs, StageType.ITT, alt)

    if spec.stage_type == "ttt":
        # 团体计时赛的赛道几乎都是平的：主办方要的是「八个人整整齐齐
        # 冲过终点」，一上山队伍立刻拆散，那画面就没了。
        # 起伏留一点点，好让队伍厚度在缓坡上体现出来。
        segs.extend(_filler(rng, total * 0.34, spec.terrain, "逆风出发段"))
        segs.append(Segment(total * 0.20, rng.uniform(0.008, 0.016),
                            name="缓上坡"))
        segs.append(Segment(total * 0.15, -rng.uniform(0.006, 0.014),
                            technical=0.15, name="缓下坡"))
        segs.extend(_filler(rng, total * 0.31, spec.terrain, "顺风冲线段"))
        return Course(name, segs, StageType.TTT, alt)

    if spec.stage_type == "cobbled":
        n_sectors = rng.randint(12, 18)
        segs.extend(_filler(rng, total * 0.30, spec.terrain, "接近段"))
        used = total * 0.30
        for i in range(n_sectors):
            sector = rng.uniform(1600, 3200)
            segs.append(Segment(sector, rng.uniform(0.004, 0.022),
                                surface=Surface.COBBLES,
                                crosswind=max(0.0, rng.gauss(3.5, 1.5)),
                                technical=rng.uniform(0.35, 0.65),
                                name=f"石板路 {n_sectors - i} 段"))
            gap = max(600.0, (total * 0.62 - n_sectors * 2400) / n_sectors)
            segs.extend(_filler(rng, gap, spec.terrain, f"过渡 {i + 1}"))
            used += sector + gap
        segs.extend(_filler(rng, max(1500.0, total - used), spec.terrain, "终点直道"))
        return Course(name, segs, StageType.COBBLED, alt)

    # --- 公路赛段：按类型决定山的数量与等级 ---
    plans = {
        "flat":          (["cat4"] if rng.random() < 0.5 else [], False),
        "hilly":         (rng.choice([["cat3", "cat3"], ["cat2", "cat3"],
                                      ["cat2", "cat2", "cat3"]]), False),
        # 真实的大环赛山地赛段累计爬升 3500-4500 米，也就是 25 m/km 上下。
        # climb_share 直接由这个数字算出来，配方给少了会让爬坡手在山地赛段
        # 也拉不开差距，总成绩最后被平路手拿走——这正是第一版跑出来的结果。
        "mountain":      (rng.choice([["hc", "cat1", "cat1"],
                                      ["cat1", "cat1", "cat1", "cat2"],
                                      ["hc", "cat1", "cat2"]]), False),
        "summit_finish": (rng.choice([["cat1", "cat1"], ["hc", "cat2"],
                                      ["cat1", "cat2", "cat2"]]), True),
    }
    categories, summit = plans[spec.stage_type]

    # 山顶终点：最后一座山不给下坡，直接冲线
    climb_blocks = [_climb(rng, c, f"{KOM_CATEGORIES[c] and c.upper()} 爬坡")
                    for c in categories]
    if summit:
        final_cat = "hc" if spec.stage_type == "summit_finish" and \
            rng.random() < 0.35 else "cat1"
        _, grade, length_km = KOM_CATEGORIES[final_cat]
        climb_blocks.append([
            Segment(length_km * 1000 * 0.62, grade * 0.92, name="终点爬坡"),
            Segment(length_km * 1000 * 0.38, grade * 1.22, name="终点陡段"),
        ])
        categories = categories + [final_cat]

    climb_len = sum(s.length_m for block in climb_blocks for s in block)
    if climb_len > total * 0.86:          # 山排太满就整体缩一点，保证放得下
        scale = total * 0.86 / climb_len
        for block in climb_blocks:
            for s in block:
                s.length_m *= scale
        climb_len = total * 0.86
    filler_total = max(total * 0.12, total - climb_len)
    n_fillers = len(climb_blocks) + 1
    each = filler_total / n_fillers

    for i, block in enumerate(climb_blocks):
        segs.extend(_filler(rng, each, spec.terrain, f"过渡 {i + 1}"))
        segs.extend(block)
    if not summit:
        segs.extend(_filler(rng, each, spec.terrain, "终点前平路"))

    # 同样归一化到配置里写的里程
    scale = total / sum(s.length_m for s in segs)
    for s in segs:
        s.length_m *= scale

    stage_type = {
        "flat": StageType.FLAT, "hilly": StageType.HILLY,
        "mountain": StageType.MOUNTAIN, "summit_finish": StageType.SUMMIT_FINISH,
    }[spec.stage_type]
    course = Course(name, segs, stage_type, alt)

    # --- 标记爬坡积分点与冲刺点 ---
    cursor = 0.0
    ci = 0
    for i, seg in enumerate(course.segments):
        cursor += seg.length_m
        if "陡段" in seg.name and ci < len(categories):
            cat = categories[ci]
            koms.append(KomPoint(cursor, seg.name.replace(" 陡段", ""), cat,
                                 KOM_CATEGORIES[cat][0]))
            ci += 1
    # 中途冲刺点：放在赛程 55%-70% 之间的平缓处
    koms.append(KomPoint(course.length_m * rng.uniform(0.55, 0.70),
                         "途中冲刺", "sprint", SPRINT_POINTS))
    course.koms = koms
    return course


def describe(course: Course) -> str:
    """一行赛道简介，用于战报和赛前界面。"""
    climbs = [k for k in course.koms if k.category != "sprint"]
    return (f"{course.length_m / 1000:.1f} km · 爬升 {course.total_ascent_m:.0f} m"
            f" · {len(climbs)} 个爬坡点")
