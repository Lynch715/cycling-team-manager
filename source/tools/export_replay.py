"""把一场比赛导出成回放数据，供前端原型播放。

    python3 source/tools/export_replay.py --stage mountain --out data/replay.json

导出的不是"结果"，是"过程"：每隔几秒记录一次每名车手的位置、速度、
剩余体力和当前动作。前端拿着它就能把比赛播出来，不需要在浏览器里
重新实现一遍物理。

只导出最后一段（默认 12 公里）的高频数据。全程逐秒对每个人采样会产生
几十兆的 JSON，而真正需要看的戏剧性几乎都发生在最后那一段。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.courses import build_course  # noqa: E402
from game.world import StageSpec, World  # noqa: E402
from sim import Race, build_peloton  # noqa: E402
from sim.pack import form_groups  # noqa: E402

PRESETS = {
    "mountain": ("summit_finish", 165.0, "alps"),
    "flat": ("flat", 182.0, "coast"),
    "hilly": ("hilly", 176.0, "italian_hills"),
    "cobbled": ("cobbled", 195.0, "cobbles"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="mountain", choices=sorted(PRESETS))
    ap.add_argument("--world", default="data/world.json")
    ap.add_argument("--out", default="data/replay.json")
    ap.add_argument("--tail-km", type=float, default=12.0)
    ap.add_argument("--sample", type=float, default=2.0, help="采样间隔（秒）")
    ap.add_argument("--riders", type=int, default=48, help="导出前多少名")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    stage_type, length, terrain = PRESETS[args.stage]
    spec = StageSpec(1, f"{args.stage} 原型赛段", stage_type, length,
                     terrain, args.seed)
    course = build_course(spec)

    # 有世界数据就用真人真队服，没有就退回程序生成的集团
    world_path = ROOT / args.world
    colors: dict[str, dict] = {}
    if world_path.exists():
        world = World.load(world_path)
        profiles = sorted(world.riders, key=lambda r: -r.overall)[:160]
        riders = [p.to_sim_rider() for p in profiles]
        meta = {}
        for p in profiles:
            t = world.team(p.team_id)
            meta[p.rider_id] = dict(
                name=p.name, nation=p.nation, role=p.role.value,
                team=t.name.split("-")[0], short=t.short_name,
                portrait=p.art_portrait, body=p.art_body,
                primary=t.color_primary, secondary=t.color_secondary,
                accent=t.color_accent, overall=p.overall,
            )
        colors = meta
    else:
        riders = build_peloton(20, 7)
        colors = {r.rider_id: dict(name=r.name, team=r.team_id, short=r.team_id,
                                   role=r.role.value, portrait="portrait-01",
                                   body="body-standard", primary="#2E3192",
                                   secondary="#FFFFFF", accent="#F5B301",
                                   overall=70, nation="")
                  for r in riders}

    race = Race(course, riders, dt=1.0, seed=args.seed)
    tail_start = course.length_m - args.tail_km * 1000
    frames: list[dict] = []

    # 手动推进而不是调用 race.run()：需要在跑的同时录制画面。
    # 关门线的判定要照抄 run() 里的逻辑，否则落后半小时的车手会
    # 一路骑到 9 小时上限，白白多跑几万步。
    winner_clock: float | None = None
    cutoff: float | None = None
    steps = 0
    while steps < race.max_steps:
        if all(s.finished or s.abandoned for s in race.states):
            break
        race.step()
        steps += 1

        if cutoff is None and race._finishers:
            cutoff = (race._finishers[0].finish_time or 0.0) * race.time_limit_frac
            winner_clock = race.clock
        if cutoff is not None and race.clock > cutoff:
            for s in race.states:
                if not s.finished:
                    s.abandoned = True
            break

        # 冠军过线两分钟后停止录制：后面还有落后半小时的大部队要走完，
        # 但那段没有观赏价值，录下来只会让回放文件膨胀十几倍。
        if winner_clock is not None and race.clock > winner_clock + 120:
            continue
        if max(s.distance for s in race.states) < tail_start:
            continue
        if race.clock % args.sample >= race.dt:
            continue
        rows = sorted((s for s in race.states if not s.abandoned),
                      key=lambda s: -s.distance)[:args.riders]
        frames.append({
            "t": round(race.clock, 1),
            "r": [[s.rider.rider_id, round(s.distance, 1),
                   round(s.speed, 2), round(s.energy.w_fraction, 3),
                   s.mode.value, s.draft_rank]
                  for s in rows],
        })

    result = race.run()
    groups_at_end = [g.size for g in form_groups(race.states)][:8]

    payload = {
        "stage": {
            "name": course.name, "type": stage_type,
            "length_m": round(course.length_m, 1),
            "ascent_m": round(course.total_ascent_m),
            "terrain": terrain,
            "parallax": spec.art_parallax,
            "landmark": spec.art_landmark,
            "profile": [[round(d), round(a, 1)]
                        for d, a in course.elevation_profile(240)],
            "koms": [{"d": round(k.distance_m), "label": k.label,
                      "cat": k.category} for k in course.koms],
        },
        "tail_start_m": round(tail_start, 1),
        "sample_s": args.sample,
        "riders": colors,
        "frames": frames,
        "result": [
            {"place": i, "id": s.rider.rider_id,
             "time": round(s.finish_time or 0, 1),
             "gap": round(result.gap_to_winner(s), 1),
             "avg_power": round(s.avg_power),
             "peak_power": round(s.peak_power)}
            for i, s, _ in result.standings()[:30]
        ],
        "events": [[round(t), text] for t, text in result.events[-25:]],
        "final_groups": groups_at_end,
    }

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"回放已导出 -> {out.relative_to(ROOT)}  ({kb:.0f} KB)")
    print(f"  赛段 {course.name}  {course.length_m / 1000:.1f} km  "
          f"爬升 {course.total_ascent_m:.0f} m")
    print(f"  {len(frames)} 帧 × {args.riders} 人，采样间隔 {args.sample} 秒")
    print(f"  冠军 {colors[result.winner.rider.rider_id]['name']}"
          f"（{colors[result.winner.rider.rider_id]['team']}）")


if __name__ == "__main__":
    main()
