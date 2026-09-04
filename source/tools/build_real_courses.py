"""用真实地理拼装赛道。

    python3 source/tools/build_real_courses.py

七条对标现实经典赛的赛段。**山是真的，赛事名是虚构的**——真实赛事名称
是注册商标，而山口名称和海拔剖面是公开地理事实。

拼装规则也照着现实来：决胜的山放在最后、中间留足够的过渡让集团重组、
石板路段按真实的密度和顺序排。这些不是装饰，它们直接决定比赛在哪一刻打起来。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.climbs import (  # noqa: E402
    cobble_sector, descent_after, flat_link, kom_for, load_climb,
)
from game.course_io import save_course, validate, course_to_dict  # noqa: E402
from game.course_report import analyse, render  # noqa: E402
from sim.course import Course, KomPoint, Segment, StageType, Surface  # noqa: E402


def assemble(name: str, stage_type: StageType, start_alt: float,
             blocks: list) -> Course:
    """把若干块拼成赛道，顺便把爬坡积分点放在每座山的顶上。"""
    segs: list[Segment] = []
    koms: list[KomPoint] = []
    cursor = 0.0
    for b in blocks:
        if isinstance(b, tuple):            # ("climb", Climb) —— 要标积分点
            _, climb = b
            for s in climb.segments:
                segs.append(s)
                cursor += s.length_m
            koms.append(kom_for(climb, cursor / 1000))
        else:
            for s in b:
                segs.append(s)
                cursor += s.length_m
    c = Course(name, segs, stage_type, start_alt)
    c.koms = koms
    return c


def build_all() -> dict[str, Course]:
    out: dict[str, Course] = {}

    # ---- 1. 阿尔卑斯女王赛段：加利比耶 + 马德莱娜 + 山顶终点 ----
    gal, mad, alp = (load_climb("galibier-valloire"),
                     load_climb("madeleine"), load_climb("alpe-duez"))
    out["real-alpine-queen"] = assemble(
        "高山之王", StageType.SUMMIT_FINISH, 720, [
            flat_link(22, "河谷出发"),
            ("climb", gal), descent_after(gal, 22, 0.6),
            flat_link(14, "谷底过渡"),
            ("climb", mad), descent_after(mad, 20, 0.5),
            flat_link(12, "布尔多瓦桑接近"),
            ("climb", alp),
        ])

    # ---- 2. 意大利双子山口：莫尔蒂罗洛 + 斯泰尔维奥 ----
    mor, ste = load_climb("mortirolo-mazzo"), load_climb("stelvio-prato")
    out["real-giro-twins"] = assemble(
        "双子山口", StageType.SUMMIT_FINISH, 550, [
            flat_link(30, "瓦尔泰利纳河谷"),
            ("climb", mor), descent_after(mor, 14, 0.7),
            flat_link(28, "谷底长距离过渡"),
            ("climb", ste),
        ])

    # ---- 3. 终极陡坡：安格利鲁山顶终点 ----
    ang = load_climb("angliru")
    out["real-angliru"] = assemble(
        "终极陡坡", StageType.SUMMIT_FINISH, 180, [
            flat_link(80, "阿斯图里亚斯起伏", headwind=1.2),
            ("climb", ang),
        ])

    # ---- 4. 北方地狱：石板路段按真实顺序与密度 ----
    order = ["haveluy", "arenberg", "orchies", "mons-en-pevele", "templeuve",
             "camphin", "carrefour-arbre", "gruson"]
    blocks = [flat_link(95, "接近段", headwind=1.5, crosswind=3.5, pieces=5)]
    for i, key in enumerate(order):
        blocks.append([cobble_sector(key)])
        gap = 12 if i < 3 else 7 if i < 6 else 4
        blocks.append(flat_link(gap, f"过渡 {i + 1}", crosswind=4.0, pieces=1))
    blocks.append(flat_link(6, "终点前", crosswind=2.0, pieces=1))
    out["real-north-hell"] = assemble("北方地狱", StageType.COBBLED, 30, blocks)

    # ---- 5. 佛兰德之环：短陡石板坡，最后 40 公里连着来三个 ----
    kwa, kop, pat = (load_climb("oude-kwaremont"), load_climb("koppenberg"),
                     load_climb("paterberg"))
    muur = load_climb("muur-geraardsbergen")
    out["real-flanders"] = assemble(
        "佛兰德之环", StageType.COBBLED, 25, [
            flat_link(70, "起步", crosswind=4.5, pieces=4),
            ("climb", muur), flat_link(18, "过渡 1", crosswind=4.0),
            ("climb", kwa), flat_link(9, "过渡 2", crosswind=3.5, pieces=1),
            ("climb", kop), flat_link(14, "过渡 3", crosswind=3.0, pieces=1),
            ("climb", kwa), flat_link(5, "过渡 4", crosswind=2.5, pieces=1),
            ("climb", pat), flat_link(13, "终点直道", crosswind=2.0, pieces=1),
        ])

    # ---- 6. 阿登之墙：拉勒杜特 + 于伊之墙终点 ----
    red, huy = load_climb("la-redoute"), load_climb("mur-de-huy")
    out["real-ardennes"] = assemble(
        "阿登之墙", StageType.HILLY, 120, [
            flat_link(85, "阿登丘陵", pieces=5),
            ("climb", red), descent_after(red, 4, 0.5),
            flat_link(28, "回环", pieces=2),
            ("climb", red), descent_after(red, 4, 0.5),
            flat_link(30, "接近于伊", pieces=2),
            ("climb", huy),
        ])

    # ---- 7. 利古里亚海岸：奇普雷萨 + 波焦，终点前 5 公里下坡 ----
    cip, pog = load_climb("cipressa"), load_climb("poggio")
    out["real-liguria"] = assemble(
        "利古里亚海岸", StageType.FLAT, 15, [
            flat_link(230, "海岸长途", headwind=1.6, crosswind=2.5, pieces=8),
            ("climb", cip), descent_after(cip, 5, 0.5),
            flat_link(9, "过渡", pieces=1),
            ("climb", pog),
            [Segment(3400, -0.042, technical=0.75, name="波焦下坡")],
            [Segment(2000, 0.002, technical=0.35, name="终点直道")],
        ])

    return out


def main() -> None:
    courses = build_all()
    print("=" * 74)
    print(f"用真实地理拼装 {len(courses)} 条赛道")
    print("山是真的（公开地理事实），赛事名是虚构的（真实赛事名是商标）")
    print("=" * 74)

    for cid, c in courses.items():
        issues = validate(course_to_dict(c, cid))
        errs = [i for i in issues if i.level == "错误"]
        save_course(c, cid)
        r = analyse(c)
        flag = "✗" if errs else "✓"
        print(f"\n{flag} {cid}")
        print(render(r))
        for i in issues[:3]:
            print(f"     [{i.level}] {i.where}：{i.text}")

    print("\n" + "=" * 74)
    print(f"已写入 data/courses/，可在赛道编辑器里打开继续调整。")


if __name__ == "__main__":
    main()
