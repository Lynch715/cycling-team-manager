"""赛道的读写与校验：让赛道变成可以手编的数据。

在这之前，25 条赛道全是程序生成的——策划改不动，玩家改不动，也没法
把真实赛事搬进来。这一层把赛道变成 JSON，程序生成从「唯一来源」降级为
「给你一份初稿」。

三条规矩：

**文件存在就以文件为准。** `load_course()` 先找 `data/courses/<id>.json`，
找不到才回退到程序生成。这样作者改过的赛道不会在下次生成时被覆盖，
而没改过的仍然自动有内容。

**校验必须挡在引擎前面。** 手编数据里出现 40% 的坡度、负数长度、
或者位置超出赛道的爬坡点，引擎会算出荒唐结果或者直接崩。所以 JSON
进引擎之前先过一遍 `validate()`，问题分「错误」和「警告」两级——
错误挡住不放行，警告只提示（有些极端赛道是作者故意的）。

**格式要能手写。** 距离用公里、坡度用百分数，而不是引擎内部的米和 tan 值。
一个人打开文件应该能直接看懂「12.4 公里、8.2%」，而不是「12400、0.082」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sim.course import Course, KomPoint, Segment, StageType, Surface

ROOT = Path(__file__).resolve().parents[2]
COURSE_DIR = ROOT / "data" / "courses"

SURFACE_CN = {"asphalt": "柏油", "cobbles": "石板", "gravel": "砾石", "wet": "湿滑"}
CN_SURFACE = {v: k for k, v in SURFACE_CN.items()}

# 校验阈值。超出「错误」区间的数据不放行；「警告」区间只提示。
LIMITS = {
    "grade":      dict(err=(-0.30, 0.30), warn=(-0.16, 0.20)),
    "length_km":  dict(err=(0.05, 60.0), warn=(0.2, 40.0)),
    "total_km":   dict(err=(3.0, 350.0), warn=(15.0, 280.0)),
    "headwind":   dict(err=(-12.0, 12.0), warn=(-6.0, 8.0)),
    "crosswind":  dict(err=(0.0, 15.0), warn=(0.0, 9.0)),
    "technical":  dict(err=(0.0, 1.0), warn=(0.0, 0.9)),
}


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------

def course_to_dict(course: Course, course_id: str = "") -> dict:
    """把赛道写成人能读、能手改的结构。

    单位刻意转成公里和百分数：作者打开文件看到的是「12.4 公里、8.2%」，
    不是「12400、0.082」。引擎内部用米和 tan 值，转换在这一层完成。
    """
    return {
        "id": course_id or course.name,
        "name": course.name,
        "stage_type": course.stage_type.value,
        "start_altitude_m": round(course.start_altitude_m),
        "segments": [
            {
                "name": s.name or "",
                "length_km": round(s.length_m / 1000, 3),
                "grade_pct": round(s.grade * 100, 2),
                "surface": SURFACE_CN[s.surface.value],
                "headwind_ms": round(s.headwind, 1),
                "crosswind_ms": round(s.crosswind, 1),
                "technical": round(s.technical, 2),
            }
            for s in course.segments
        ],
        "koms": [
            {"at_km": round(k.distance_m / 1000, 2), "label": k.label,
             "category": k.category, "points": list(k.points)}
            for k in course.koms
        ],
    }


def course_from_dict(d: dict) -> Course:
    segs = [
        Segment(
            length_m=float(s["length_km"]) * 1000,
            grade=float(s.get("grade_pct", 0.0)) / 100.0,
            surface=Surface(CN_SURFACE.get(s.get("surface", "柏油"),
                                           s.get("surface", "asphalt"))),
            headwind=float(s.get("headwind_ms", 0.0)),
            crosswind=float(s.get("crosswind_ms", 0.0)),
            technical=float(s.get("technical", 0.0)),
            name=s.get("name", ""),
        )
        for s in d["segments"]
    ]
    c = Course(d.get("name", "未命名赛道"), segs,
               StageType(d.get("stage_type", "flat")),
               float(d.get("start_altitude_m", 100)))
    c.koms = [
        KomPoint(float(k["at_km"]) * 1000, k.get("label", ""),
                 k.get("category", "cat3"), tuple(k.get("points", ())))
        for k in d.get("koms", [])
    ]
    return c


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

@dataclass
class Issue:
    level: str          # "错误" / "警告"
    where: str
    text: str


def validate(d: dict) -> list[Issue]:
    """把一份手编赛道过一遍。返回问题清单，空表示可以直接用。"""
    issues: list[Issue] = []

    segs = d.get("segments") or []
    if not segs:
        issues.append(Issue("错误", "整体", "没有任何路段"))
        return issues

    total = 0.0
    for i, s in enumerate(segs, start=1):
        where = f"第 {i} 段" + (f"「{s.get('name')}」" if s.get("name") else "")

        try:
            length = float(s["length_km"])
            grade = float(s.get("grade_pct", 0.0)) / 100.0
        except (KeyError, TypeError, ValueError):
            issues.append(Issue("错误", where, "长度或坡度不是数字"))
            continue
        total += length

        for key, val, unit in (("length_km", length, "km"),
                               ("grade", grade, ""),
                               ("headwind", float(s.get("headwind_ms", 0)), "m/s"),
                               ("crosswind", float(s.get("crosswind_ms", 0)), "m/s"),
                               ("technical", float(s.get("technical", 0)), "")):
            lim = LIMITS[key]
            lo, hi = lim["err"]
            wlo, whi = lim["warn"]
            show = f"{val * 100:.1f}%" if key == "grade" else f"{val:g}{unit}"
            if not (lo <= val <= hi):
                issues.append(Issue("错误", where,
                                    f"{key} = {show}，超出可接受范围 "
                                    f"{lo}–{hi}"))
            elif not (wlo <= val <= whi):
                issues.append(Issue("警告", where,
                                    f"{key} = {show}，非常极端"
                                    f"（常见范围 {wlo}–{whi}）"))

        sur = s.get("surface", "柏油")
        if sur not in CN_SURFACE and sur not in SURFACE_CN:
            issues.append(Issue("错误", where, f"未知路面「{sur}」"))

    lim = LIMITS["total_km"]
    if not (lim["err"][0] <= total <= lim["err"][1]):
        issues.append(Issue("错误", "整体",
                            f"总长度 {total:.1f} km，超出 "
                            f"{lim['err'][0]}–{lim['err'][1]}"))
    elif not (lim["warn"][0] <= total <= lim["warn"][1]):
        issues.append(Issue("警告", "整体", f"总长度 {total:.1f} km 不太常见"))

    for k in d.get("koms", []):
        at = float(k.get("at_km", 0))
        if at < 0 or at > total + 0.01:
            issues.append(Issue("错误", f"积分点「{k.get('label', '')}」",
                                f"位置 {at:.1f} km 超出了赛道长度 {total:.1f} km"))

    try:
        StageType(d.get("stage_type", "flat"))
    except ValueError:
        issues.append(Issue("错误", "整体",
                            f"未知赛段类型「{d.get('stage_type')}」"))

    return issues


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------

def course_path(course_id: str) -> Path:
    return COURSE_DIR / f"{course_id}.json"


def save_course(course: Course, course_id: str) -> Path:
    COURSE_DIR.mkdir(parents=True, exist_ok=True)
    p = course_path(course_id)
    p.write_text(json.dumps(course_to_dict(course, course_id),
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_course(course_id: str, fallback=None, strict: bool = True) -> Course:
    """按 id 读赛道。文件不存在时用 fallback（通常是程序生成）。

    strict=True 时，校验出「错误」级问题会抛异常而不是硬着头皮跑——
    带着一个 40% 坡度的赛道跑出来的结果毫无意义，早点报错比晚点困惑好。
    """
    p = course_path(course_id)
    if not p.exists():
        if fallback is None:
            raise FileNotFoundError(f"找不到赛道 {course_id}，也没有提供 fallback")
        return fallback()

    d = json.loads(p.read_text(encoding="utf-8"))
    issues = validate(d)
    errors = [i for i in issues if i.level == "错误"]
    if errors and strict:
        lines = "\n".join(f"  · {i.where}：{i.text}" for i in errors[:8])
        raise ValueError(f"赛道 {course_id} 有 {len(errors)} 处错误：\n{lines}")
    return course_from_dict(d)


def list_courses() -> list[str]:
    if not COURSE_DIR.exists():
        return []
    return sorted(p.stem for p in COURSE_DIR.glob("*.json"))
