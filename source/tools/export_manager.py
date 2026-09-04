"""导出经理原型所需的数据包，并生成可双击打开的 HTML。

    python3 source/tools/export_manager.py --team T08

原型要能让人真的"当一次经理"：看阵容、给赛前指令、推进赛季、读战报和事件。
所有比赛结果都用快速结算在浏览器里播不了——那是 Python 的活——所以这里
预先把**同一场比赛在五种打法下的结果**全部算好塞进数据包。玩家在界面上
换打法，看到的是真跑出来的结果，不是编的。

五种打法 × 若干场比赛都用完整引擎跑太慢，所以用快速结算；两者已由
calibrate_quick.py 对账过，描述的是同一个世界。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.courses import build_course, describe  # noqa: E402
from game.events import EVENTS, When, apply, fill, pick_event  # noqa: E402
from game.orders import PLAYBOOKS, Order, apply_playbook  # noqa: E402
from game.quickresolve import RACE_SIGMA, resolve  # noqa: E402
from game.season import pick_entrants, terrain_bias  # noqa: E402
from game.world import World  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "manager_template.html"

# 打法对快速结算的影响。完整引擎里这些是行为乘数，快速结算里压缩成
# 对"能力值"和"当日波动"的修正——两边描述的是同一件事。
BOOK_EFFECT = {
    "总成绩优先": dict(leader=1.020, sprinter=0.985, spread=0.95),
    "冲刺夺段":   dict(sprinter=1.030, leadout=1.010, leader=0.985, spread=1.00),
    "全员抢突围": dict(breakaway=1.045, domestique=1.020, sprinter=0.955,
                       leader=0.975, spread=1.30),
    "山地强攻":   dict(climber=1.035, leader=1.020, sprinter=0.950, spread=1.15),
    "保存实力":   dict(spread=0.80, leader=0.985, sprinter=0.985,
                       climber=0.985, breakaway=0.985),
}


def run_one(world, event, stage_idx, my_team, book, seed):
    """用某个打法跑一个赛段，返回名次与我队的表现。"""
    rng = random.Random(seed)
    entries = pick_entrants(world, event, rng)
    entries[my_team] = [r.rider_id for r in world.roster(my_team)][:8]
    ids = [rid for v in entries.values() for rid in v]
    profiles = [world.rider(i) for i in ids]
    sim_riders = [p.to_sim_rider() for p in profiles]

    spec = event.stages[stage_idx]
    course = build_course(spec, name_prefix=f"{event.name} ")

    eff = BOOK_EFFECT.get(book, {})
    frng = random.Random(seed + 11)
    sigma = RACE_SIGMA[spec.stage_type] * eff.get("spread", 1.0)
    form = {}
    for p in profiles:
        m = math.exp(frng.gauss(0.0, sigma))
        if p.team_id == my_team:
            m *= eff.get(p.role.value, 1.0)
        form[p.rider_id] = m

    q = resolve(sim_riders, course, spec.stage_type, random.Random(seed), form=form)
    win_t = q.times[q.order[0]]
    rows = []
    for place, rid in enumerate(q.order[:25], start=1):
        p = world.rider(rid)
        t = world.team(p.team_id)
        rows.append({
            "place": place, "name": p.name, "role": p.role.value,
            "team": t.name.split("-")[0], "color": t.color_primary,
            "gap": round(q.times[rid] - win_t, 1),
            "mine": p.team_id == my_team,
        })
    mine = [(i + 1, world.rider(r)) for i, r in enumerate(q.order)
            if world.rider(r).team_id == my_team]
    return {
        "rows": rows,
        "best": mine[0][0] if mine else 999,
        "best_name": mine[0][1].name if mine else "",
        "top10": sum(1 for pl, _ in mine if pl <= 10),
        "winner_time": round(win_t),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default="data/world.json")
    ap.add_argument("--team", default="T08", help="玩家执教的车队")
    ap.add_argument("--races", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260829)
    args = ap.parse_args()

    world = World.load(ROOT / args.world)
    my = world.team(args.team)
    rng = random.Random(args.seed)

    # 挑六场有代表性的比赛：不同地形、不同等级
    picks, seen_terrain = [], set()
    for e in world.calendar:
        st = e.stages[0].stage_type
        key = (st, e.tier.value)
        if key in seen_terrain:
            continue
        seen_terrain.add(key)
        picks.append(e)
        if len(picks) >= args.races:
            break

    races = []
    for i, e in enumerate(picks):
        spec = e.stages[0]
        course = build_course(spec, name_prefix=f"{e.name} ")
        results = {}
        for book in PLAYBOOKS:
            results[book] = run_one(world, e, 0, args.team, book,
                                    args.seed + i * 97)
        races.append({
            "race_id": e.race_id, "name": e.name, "tier": e.tier.value,
            "day": e.start_day, "prestige": e.prestige,
            "stage_type": spec.stage_type,
            "desc": describe(course),
            "length_km": round(course.length_m / 1000, 1),
            "ascent_m": round(course.total_ascent_m),
            "climb_bias": round(terrain_bias(e), 2),
            "landmark": spec.art_landmark,
            "profile": [[round(d), round(a, 1)]
                        for d, a in course.elevation_profile(160)],
            "results": results,
        })

    # 三个事件，玩家可以真的做选择——后果在数据里预先算好两三条分支
    events = []
    seen = set()
    for _ in range(3):
        e = pick_event(When.AFTER_RACE, rng, seen)
        seen.add(e.event_id)
        who = rng.choice(world.roster(args.team))
        branches = []
        for j, c in enumerate(e.choices):
            snapshot = (my.budget, my.prestige, who.morale)
            res = apply(e, j, world, my, who, random.Random(args.seed + j))
            branches.append({
                "label": fill(c.label, who, my),
                "outcome": res.outcome,
                "changes": res.changes,
            })
            my.budget, my.prestige, who.morale = snapshot   # 回滚，只做展示
        events.append({
            "title": e.title, "category": e.category.value,
            "text": fill(e.text, who, my, picks[0].name),
            "art": e.art, "rider": who.name,
            "branches": branches,
        })

    roster = []
    for r in sorted(world.roster(args.team), key=lambda r: -r.overall):
        roster.append({
            "id": r.rider_id, "name": r.name, "nation": r.nation,
            "age": r.age, "role": r.role.value, "overall": r.overall,
            "potential": r.potential, "salary": r.salary,
            "contract": r.contract_years, "mass": r.body_mass_kg,
            "portrait": r.art_portrait, "form": r.form, "morale": r.morale,
            "attrs": {k: getattr(r.attributes, k)
                      for k in ("flat", "climbing", "sprint", "time_trial",
                                "descending", "endurance", "recovery",
                                "positioning", "resilience")},
        })

    payload = {
        "season": world.season,
        "team": {
            "id": my.team_id, "name": my.name, "short": my.short_name,
            "division": my.division.value, "budget": my.budget,
            "prestige": my.prestige, "badge": my.art_badge,
            "primary": my.color_primary, "secondary": my.color_secondary,
            "accent": my.color_accent,
            "payroll": sum(r["salary"] for r in roster),
        },
        "roster": roster,
        "races": races,
        "events": events,
        "playbooks": {k: {kk.value: vv.value for kk, vv in v.items()}
                      for k, v in PLAYBOOKS.items()},
        "orders": [o.value for o in Order],
    }

    data_path = ROOT / "data" / "manager.json"
    data_path.write_text(json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":")), encoding="utf-8")

    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))
    out = ROOT / "原型_车队经理.html"
    out.write_text(html, encoding="utf-8")

    print(f"经理原型已生成 -> {out.name}（{out.stat().st_size / 1024:.0f} KB）")
    print(f"  执教车队：{my.name}（{my.division.value}）"
          f"预算 {my.budget} 万 / 薪资 {payload['team']['payroll']} 万")
    print(f"  {len(roster)} 名车手，{len(races)} 场比赛 × "
          f"{len(PLAYBOOKS)} 种打法 = {len(races) * len(PLAYBOOKS)} 组预算结果")
    print(f"  {len(events)} 个事件，共 "
          f"{sum(len(e['branches']) for e in events)} 条分支")


if __name__ == "__main__":
    main()
