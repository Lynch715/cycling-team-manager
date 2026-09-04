"""亲自看一场比赛：用完整引擎跑，并把过程录下来。

项目里最强的一块是那台逐秒物理引擎——突围怎么形成、主集团怎么收网、
谁在最后一个坡上被甩掉，全都是从物理里涌现出来的，没有一行脚本。
但在游戏里，玩家点「出发」，一秒之后看到一张成绩表。**那台引擎一次也没
被玩家看见过。**

这一层把它接出来。代价是时间：完整引擎跑一个赛段要二三十秒，不可能每场
都跑。所以它是玩家主动选的——「这一场我要亲自看」——其余的仍然走快速结算。

### 录什么

不录一百六十个车手的坐标。**电视转播也不这么给**：镜头给的是「突围三人，
领先两分四十；主集团在后面，我方队长在集团里」。所以录的是**集团和差距**，
外加玩家自己车手的位置——这既是正确的抽象，也让回放数据小两个数量级。

### 一条硬规矩：看到的必须算数

玩家看完一场比赛，结果必须就是这场比赛的结果。如果看完之后系统再用快速
结算重跑一遍，那他刚才看的那四十分钟就是一场表演。所以录制返回的名次和
用时直接进入总成绩——**完整引擎在这里不是特效，是判决。**
"""

from __future__ import annotations

from sim.pack import form_groups
from sim.race import Race

# 采样间隔（比赛秒）。五小时的赛段按 45 秒采一帧约 400 帧，
# 前端用 25 帧/秒播完是十六秒——比赛的节奏感在，文件也不到 200 KB。
SAMPLE_S = 45.0

KIND_CN = {"attack": "进攻", "sprint": "冲刺", "survive": "掉队"}


def _group_kind(g, main_size: int) -> str:
    """给一个集团起个观众看得懂的名字。"""
    if g.size >= main_size:
        return "主集团"
    if g.size <= 3:
        return "突围" if g.size > 1 else "单飞"
    return "突围集团" if g.size <= 12 else "分裂集团"


def _pick_events(events, cap: int = 200) -> list:
    """挑出要播报的事件。

    不能简单取最后 N 条——引擎一场比赛能产出好几百条，而它们不是均匀分布的，
    终点前二十公里最密集。取尾部的话，回放的前四个小时播报栏一片空白，
    而那恰恰是突围形成、集团放走的那段。**要的是整场都有东西看，
    不是终点前挤成一团。** 所以超量时按时间均匀抽稀。
    """
    rows = [[round(t), text] for t, text in events]
    if len(rows) <= cap:
        return rows
    step = len(rows) / cap
    return [rows[int(i * step)] for i in range(cap)]


def record(course, sim_riders, seed: int, rain: float,
           mine: set[str], names: dict[str, str],
           sample_s: float = SAMPLE_S, terrain: str = "coast",
           colors: dict | None = None) -> tuple[list, dict, list, dict]:
    """跑一个赛段并录像。

    返回 (名次, 用时, 退赛, 回放数据)——前三项和 `run_stage` 走完整引擎时
    完全一样，所以调用方可以直接拿去算总成绩。
    """
    race = Race(course, sim_riders, dt=1.0, seed=seed, rain=rain)

    roster = [rid for rid in names if rid in mine]
    idx = {rid: i for i, rid in enumerate(roster)}

    frames: list[dict] = []
    winner_clock: float | None = None
    cutoff: float | None = None
    steps = 0

    while steps < race.max_steps:
        if all(s.finished or s.abandoned for s in race.states):
            break
        race.step()
        steps += 1

        # 关门线的判定要照抄 run() 里的那一份，否则落后半小时的车手会一路
        # 骑到九小时上限，白跑几万步。
        if cutoff is None and race._finishers:
            cutoff = (race._finishers[0].finish_time or 0.0) * race.time_limit_frac
            winner_clock = race.clock
        if cutoff is not None and race.clock > cutoff:
            for s in race.states:
                if not s.finished:
                    s.abandoned = True
            break
        # 冠军过线一分钟之后没有观赏价值了，后面还有大部队要慢慢走完
        if winner_clock is not None and race.clock > winner_clock + 60:
            continue
        if race.clock % sample_s >= race.dt:
            continue

        live = [s for s in race.states if not s.abandoned and not s.finished]
        if not live:
            continue
        groups = form_groups(live)
        if not groups:
            continue
        main_size = max(g.size for g in groups)
        head_d = max(s.distance for s in live)

        # 取最前面四个集团，外加主集团——**主集团必须永远在画面上**。
        # 只取前几个的话，比赛中段前面全是零散的突围和掉队者，
        # 一百多人的主集团反而不见了，观众看不到「后面追得怎么样」。
        main = max(groups, key=lambda x: x.size)
        show = list(groups[:4])
        if main not in show:
            show.append(main)

        gs = []
        for g in show:
            lead = max(s.distance for s in g.members)
            # 差距用「领先者的当前速度」折算成秒，这是转播里报的那个数
            v = max(4.0, sum(s.speed for s in g.members) / g.size)
            gs.append({
                "n": g.size,
                "km": round(lead / 1000.0, 2),
                "gap": round((head_d - lead) / v, 1),
                "kind": _group_kind(g, main_size),
                "mine": sum(1 for s in g.members if s.rider.rider_id in mine),
                # 画面上一个集团最多画十几个人，颜色取前几个就够
                "tc": [s.rider.team_id for s in
                       sorted(g.members, key=lambda x: -x.distance)[:14]],
            })

        frames.append({
            "t": round(race.clock),
            "km": round(head_d / 1000.0, 2),
            "groups": gs,
            # 车手名字每帧重复一遍会让回放大一倍——名字只在 payload 顶层
            # 给一次，帧里只放下标。
            "mine": [[idx[s.rider.rider_id],
                      round((head_d - s.distance) / max(4.0, s.speed)),
                      round(s.energy.w_fraction, 2),
                      KIND_CN.get(s.mode.value, "")]
                     for s in live if s.rider.rider_id in mine],
        })

    result = race.run()
    order = [s.rider.rider_id for s in result.finishers]
    times = {s.rider.rider_id: (s.finish_time or 0.0) for s in result.finishers}
    dnf = [s.rider.rider_id for s in result.dnf]

    replay = {
        "name": course.name,
        "terrain": terrain,
        # 每个集团画出来要有队服颜色。给一份「集团里出现过的队伍主色」，
        # 前端就能把突围里那三个人画成三种不同的队服。
        "colors": colors or {},
        "km": round(course.length_m / 1000.0, 1),
        "ascent": round(course.total_ascent_m),
        "koms": [{"km": round(k.distance_m / 1000.0, 1), "label": k.label}
                 for k in course.koms],
        "sample_s": sample_s,
        "mine_names": [names[rid] for rid in roster],
        "frames": frames,
        "events": _pick_events(result.events),
    }
    return order, times, dnf, replay
