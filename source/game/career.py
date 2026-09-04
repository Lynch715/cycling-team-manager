"""生涯：把所有系统串成一条能连续玩下去的时间线。

在这之前，每个系统都能单独跑，但没有任何东西把它们连起来——没有存档，
没有"下一步该干什么"，玩家关掉终端一切归零。这一层就是那根线。

三个设计决定：

**一切通过一个 API 推进。** `advance()` 往前走一步，返回"发生了什么"和
"现在轮到你决定什么"。CLI、HTML 前端、以后的 Unity 都调同一个函数，
不会出现两套逻辑对不上的情况。

**决策是排队的，不是弹窗打断的。** 赛前指令、事件选择、转会报价全部进
同一个待决策队列。玩家可以攒着一起处理，也可以让系统按默认值自动跑完
——后者对"我只想看结果"的玩家非常重要。

**存档是纯数据。** 整个生涯序列化成一个 JSON，没有对象引用、没有闭包。
这样存档能跨版本读、能手动改、能上传分享，也方便排查线上问题。
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from game.events import EVENTS, BY_ID, When, apply as apply_event, fill, pick_event
from game.generate_world import generate
from game.management import (
    TransferOutcome, advance_season, finish_offseason, market_value,
    relegation_table, rolling_points,
)
from game.market import (
    Offer, asking_price, counter_offer, evaluate_offer, open_market,
    ai_offers, resolve_offers, Listing,
)
from game.orders import Order, PLAYBOOKS, apply_playbook, build_directives

_ORDER_BY_NAME = {o.value: o for o in Order}
from game.training import (
    CAMPS, CAMP_BY_KEY, PROGRAMS, Plan, default_plan,
    run_offseason_training,
)
from game.records import connect, record_season, record_stage
from game.season import EventOutcome, rest, run_event, squad_size
from game.world import Division, World

SAVE_VERSION = 2


# 角色的中文名。放在这里而不是前端，是因为它会进 Decision 的文案和动态流——
# 那些是玩家直接读到的字，不该由前端再猜一次。
ROLE_CN = {
    "sprinter": "冲刺手", "climber": "爬坡手", "rouleur": "全能型",
    "leader": "总成绩核心", "domestique": "工兵", "leadout": "冲刺列车",
    "breakaway": "突围手",
}


class Phase(str, Enum):
    PRESEASON = "赛季准备"
    RACING = "赛季进行中"
    OFFSEASON = "休赛期"


class DecisionKind(str, Enum):
    ORDERS = "赛前指令"
    EVENT = "俱乐部事务"
    TRANSFER = "转会决定"
    TRAINING = "训练安排"


@dataclass
class Decision:
    """一件等着玩家拍板的事。

    `options` 是可选项的展示文本，`default` 是玩家不管时系统会选的那个——
    这一条让"快速推进整个赛季"成为可能，而不必强迫玩家点完每一个弹窗。
    """

    kind: DecisionKind
    title: str
    detail: str
    options: list[str]
    default: int = 0
    payload: dict = field(default_factory=dict)
    resolved: int | None = None


@dataclass
class Headline:
    """给玩家看的一条动态。存档里保留最近的若干条，构成赛季叙事。"""

    day: int
    text: str
    tag: str = ""


class Career:
    """一次生涯存档。"""

    def __init__(self, world: World, team_id: str, seed: int = 0) -> None:
        self.world = world
        self.team_id = team_id
        self.seed = seed
        self.rng = random.Random(seed)
        self.day = 0
        self.phase = Phase.PRESEASON
        self.race_index = 0
        self.pending: list[Decision] = []
        self.headlines: list[Headline] = []
        self.season_log: list[Headline] = []   # 只装本赛季的，不受总量上限影响
        self.season_results: list[dict] = []
        self.history: list[dict] = []        # 每个赛季的总结
        self.playbook: str = "总成绩优先"
        self.seen_events: set[str] = set()
        self._points: dict[str, int] = {}
        self._transfers: TransferOutcome | None = None
        self._race_days: dict[str, int] = {}
        self._wins: dict[str, int] = {}
        self.db = None                    # 由 attach_db() 挂上，不挂就不记录
        # 最近一场比赛的完整结果。命令行不需要它——跑完打一行战报就够了——
        # 但界面上「刚才那场比赛到底发生了什么」是玩家唯一真正在意的东西，
        # 只给一句「我队最好成绩第 7 名」等于什么都没说。
        self.last_race: dict | None = None
        # 玩家给单个车手下的指令，覆盖 playbook 铺开的那一份。
        # playbook 是「一键铺开」，这里是「我要亲手管这两个人」——
        # 经理游戏里真正好玩的是后者，前者只是让你不必管完八个人。
        self.rider_orders: dict[str, str] = {}
        # 下一场要亲自看的赛段序号；None 表示交给快速结算
        # 玩家为下一场排的名单。空表示交给系统按适配度自动挑。
        self.lineup: list[str] = []
        self.watch_index: int | None = None
        self.last_replay: dict | None = None

    def attach_db(self, path=None):
        """挂上成绩数据库。不挂也能玩，只是没有历史。"""
        self.db = connect(path)
        return self.db

    # ---- 便捷访问 ------------------------------------------------------

    @property
    def team(self):
        return self.world.team(self.team_id)

    @property
    def roster(self):
        return self.world.roster(self.team_id)

    @property
    def calendar(self):
        return sorted(self.world.calendar, key=lambda e: e.start_day)

    @property
    def next_race(self):
        cal = self.calendar
        return cal[self.race_index] if self.race_index < len(cal) else None

    def set_playbook(self, name: str) -> str:
        """赛前改打法。和赛季初的那次选择是同一个开关，只是可以随时拧。

        换 playbook 会清掉逐人指令——否则玩家换了整体打法，却发现有两个
        人还在执行上一套里的旧命令，而界面上没有任何地方说明这件事。
        """
        if name in PLAYBOOKS:
            self.playbook = name
            self.rider_orders = {}
        return self.playbook

    def squad_size(self) -> int:
        e = self.next_race
        return squad_size(e) if e else 0

    def set_lineup(self, ids: list[str]) -> list[str]:
        """排下一场的名单。传空表示交回给系统自动挑。

        没被派上场的人在这段时间里恢复疲劳——**这才是「轮换阵容」这件事
        在数值上的意义**。在疲劳恢复被修好之前（生涯模式从来没调过
        `rest()`，全世界跑到赛季中段一起顶到 1.0），这个决定是没有后果的。
        """
        valid = {r.rider_id for r in self.roster}
        self.lineup = [i for i in ids if i in valid][:self.squad_size()]
        return self.lineup

    def set_order(self, rider_id: str, order: str | None) -> None:
        """给一名车手单独下令。传 None 表示交回给 playbook。"""
        if order is None or order == "":
            self.rider_orders.pop(rider_id, None)
        elif order in _ORDER_BY_NAME:
            self.rider_orders[rider_id] = order

    def current_orders(self) -> dict:
        """本场实际生效的指令：playbook 铺一遍，再让逐人指令覆盖。"""
        out = apply_playbook(self.playbook, self.roster)
        for rid, name in self.rider_orders.items():
            if rid in out:
                out[rid] = _ORDER_BY_NAME[name]
        return out

    def log(self, text: str, tag: str = "") -> None:
        h = Headline(self.day, text, tag)
        self.headlines.append(h)
        self.season_log.append(h)
        # 总的动态流有上限，但本赛季的那一份完整保留——
        # 否则赛季回顾会因为被截断而莫名其妙地空掉。
        self.headlines = self.headlines[-160:]

    # ---- 推进 ----------------------------------------------------------

    def advance(self) -> list[Decision]:
        """往前走一步。返回这一步新产生的、等着玩家拍板的事。

        有未决决策时不会往前走——先把手上的事处理完。这比"边跑边弹窗"
        更容易理解，也让前端不需要处理并发状态。
        """
        if self.pending:
            return self.pending

        if self.phase is Phase.PRESEASON:
            return self._start_season()
        if self.phase is Phase.RACING:
            return self._run_next_race()
        return self._do_offseason()

    def resolve(self, index: int, choice: int) -> None:
        """拍板一个待决策。"""
        d = self.pending[index]
        d.resolved = choice
        if d.kind is DecisionKind.ORDERS:
            self.playbook = d.options[choice]
        elif d.kind is DecisionKind.TRAINING:
            self._do_training(d, choice)
        elif d.kind is DecisionKind.TRANSFER:
            self._do_transfer(d, choice)
        elif d.kind is DecisionKind.EVENT:
            ev = BY_ID[d.payload["event_id"]]
            who = self.world.rider(d.payload["rider_id"])
            res = apply_event(ev, choice, self.world, self.team, who, self.rng)
            self.log(f"{ev.title}：{res.outcome}", "事务")
            for c in res.changes:
                self.log(f"　　{c}", "变化")
        self.pending = [x for x in self.pending if x.resolved is None]

    def auto_resolve(self) -> None:
        """把手上所有待决策按默认值拍掉。用于"快速推进"。"""
        while self.pending:
            self.resolve(0, self.pending[0].default)

    # ---- 训练 ----------------------------------------------------------

    def _queue_training(self) -> None:
        roster = self.roster
        cost = {c.key: c.cost_per_rider * len(roster) for c in CAMPS}
        opts = [f"{c.name}（{cost[c.key]} 万）— {c.blurb}" for c in CAMPS]
        self.pending.append(Decision(
            DecisionKind.TRAINING, "休赛期训练",
            f"全队 {len(roster)} 人。集训营按人头收费，"
            f"当前资金 {self.team.budget} 万。每个人的专项方案沿用上赛季的"
            f"设定（可在阵容页逐个调整）。",
            opts, default=0,
            payload={"camp_keys": [c.key for c in CAMPS]},
        ))

    def set_training(self, rider_id: str, program: str,
                     intensity: str | None = None) -> None:
        """给单个车手指定训练方案。前端在阵容页调这个。"""
        r = self.world.rider(rider_id)
        plan = Plan.from_dict(r.training) if r.training else default_plan(r)
        plan.program = program
        if intensity:
            from game.training import Intensity
            plan.intensity = Intensity(intensity)
        r.training = plan.to_dict()

    def _do_training(self, d: Decision, choice: int) -> None:
        camp_key = d.payload["camp_keys"][choice]
        camp = CAMP_BY_KEY[camp_key]
        plans = {r.rider_id: (Plan.from_dict(r.training) if r.training
                              else default_plan(r)) for r in self.roster}
        results, cost = run_offseason_training(
            self.world, self.team_id, plans, camp_key, self._race_days, self.rng)
        self.team.budget = max(50, self.team.budget - cost)

        gained = sorted(results, key=lambda x: -x.overall_delta)[:3]
        self.log(f"休赛期训练：{camp.name}，花费 {cost} 万。", "训练")
        for g in gained:
            if g.overall_delta:
                self.log(f"　　{g.rider_name} 总评 {g.overall_delta:+d}", "训练")
        for g in results:
            if g.injured:
                self.log(f"　　⚠ {g.rider_name} {g.note}", "训练")

    # ---- 转会窗 --------------------------------------------------------

    def shortlist(self, limit: int = 6) -> list[Listing]:
        """按"队里最缺什么"给玩家排一份候选名单，而不是简单按总评排。

        直接按总评排会把玩家推向"永远买最贵的"，那不是经理该做的判断。
        """
        from game.management import squad_need

        roster = self.roster
        need = squad_need(self.team, roster)
        payroll = sum(r.salary for r in roster)
        room = self.team.budget * 1.15 - payroll

        out = []
        for l in open_market(self.world, self.rng):
            # 显示"真正签得下来要多少钱"，而不是经纪人的开价。
            # 只报开价的话，玩家每次都会被别的队加价截胡，而且永远
            # 不知道自己差在哪——那不是难度，那是信息不透明。
            probe = Offer(l.rider.rider_id, self.team_id, l.asking, 3)
            l.asking = max(l.asking, counter_offer(l.rider, self.team,
                                                   probe, self.world))
            if l.asking > room:
                continue
            fit = (need.get(l.rider.role, 0.1) + 0.3) * l.rider.overall ** 1.6
            out.append((fit / max(1, l.asking) ** 0.7, l))
        out.sort(key=lambda x: -x[0])
        return [l for _, l in out[:limit]]

    def _queue_transfer(self) -> None:
        roster = self.roster
        if len(roster) >= 9:
            self._close_transfer_window()
            return
        picks = self.shortlist()
        if not picks:
            self._close_transfer_window()
            return

        payroll = sum(r.salary for r in roster)
        room = int(self.team.budget * 1.15 - payroll)
        opts = [f"签下 {l.rider.name}（{ROLE_CN.get(l.rider.role.value, l.rider.role.value)}"
                f" · 总评 {l.rider.overall} · {l.asking} 万 · {l.rider.age} 岁）"
                for l in picks]
        opts.append("结束转会窗")
        self.pending.append(Decision(
            DecisionKind.TRANSFER, "转会市场",
            f"阵容 {len(roster)} 人，薪资空间还剩 {room} 万。"
            f"标价是「按他的标准，我们这支队要签下他大概需要多少」——"
            f"已经把队伍档次和出场机会折算进去了。即便如此，"
            f"如果有别的队出价更高，他仍然会去那边。",
            opts, default=len(opts) - 1,
            payload={"rider_ids": [l.rider.rider_id for l in picks],
                     "askings": [l.asking for l in picks]},
        ))

    def _do_transfer(self, d: Decision, choice: int) -> None:
        ids = d.payload["rider_ids"]
        if choice >= len(ids):
            self._close_transfer_window()
            return
        rid, ask = ids[choice], d.payload["askings"][choice]
        rider = self.world.rider(rid)

        # 玩家按标价出手，再加一点点溢价——标价是"刚好能说服他"的数字，
        # 一分不多的话任何一个抬价的对手都能截胡。
        mine = Offer(rid, self.team_id, int(ask * 1.08), 3, from_player=True)

        # 竞争者：不是每支队都会跟。只有真的缺这个位置的队才会加价，
        # 而且只有六成的时候会真的动手。
        #
        # 早期版本让每一次玩家报价都触发一场全联盟竞拍，结果玩家八次
        # 报价八次失败——那不是难度，那是设计上把玩家排除在系统之外。
        rivals = []
        if self.rng.random() < 0.60:
            rivals = [o for o in ai_offers(
                self.world, [Listing(rider, ask)], self.rng,
                exclude_team=self.team_id)
                if o.rider_id == rid and o.salary > mine.salary][:2]
        signings = resolve_offers(self.world, [mine] + rivals, self.rng)

        if signings and rider.team_id == self.team_id:
            s = signings[0]
            beat = ("，击败了 " + "、".join(s.beat)) if s.beat else ""
            self.log(f"✍ 签下 {rider.name}（"
                     f"{ROLE_CN.get(rider.role.value, rider.role.value)} "
                     f"{rider.overall}分），{s.salary} 万 / {s.years} 年{beat}",
                     "转会")
        elif rider.team_id:
            self.log(f"✕ {rider.name} 去了 "
                     f"{self.world.team(rider.team_id).name.split('-')[0]}，"
                     f"我们的报价没有竞争力", "转会")
        else:
            want = counter_offer(rider, self.team, mine, self.world)
            self.log(f"✕ {rider.name} 拒绝了 {ask} 万的报价，"
                     f"他的经纪人说至少要 {want} 万", "转会")
        self._queue_transfer()

    def _close_transfer_window(self) -> None:
        if self._transfers is None:
            return
        rep = finish_offseason(self.world, self.rng, self._transfers,
                               self.team_id)
        self._transfers = None
        t = rep["transfers"]
        self.log(f"转会窗关闭：全联盟退役 {len(t.retirements)} 人，"
                 f"签约 {len(t.signings)} 人，无人问津 {len(t.unsigned)} 人。",
                 "转会")

    # ---- 各阶段 --------------------------------------------------------

    def _start_season(self) -> list[Decision]:
        self.phase = Phase.RACING
        self.race_index = 0
        self._points = {}
        self.season_results = []
        self.seen_events = set()
        self.season_log = []
        self._wins = {}
        self.log(f"{self.world.season} 赛季开始。{self.team.name} 以 "
                 f"{self.team.division.value} 身份参赛，预算 "
                 f"{self.team.budget} 万，薪资 "
                 f"{sum(r.salary for r in self.roster)} 万。", "赛季")
        self.pending.append(Decision(
            DecisionKind.ORDERS, "本赛季的整体打法",
            "可以随时在赛前调整。这决定了车手的进攻欲望、领骑意愿、"
            "以及舍不舍得烧无氧储备。",
            list(PLAYBOOKS), default=0,
        ))
        return self.pending

    def _run_next_race(self) -> list[Decision]:
        event = self.next_race
        if event is None:
            self.phase = Phase.OFFSEASON
            return self.advance()

        # 上一场结束到这一场开赛之间的空档：所有人回血。
        # 少了这一句，跑到赛季中段全世界的疲劳都会顶满并一直顶到年底。
        rest(self.world, max(0, event.start_day - self.day))
        self.day = event.start_day + event.days
        orders = self.current_orders()

        def rec(ev, idx, result):
            if self.db is None:
                return
            record_stage(self.db, self.world.season, ev.race_id, ev.name,
                         ev.tier.value, idx + 1,
                         f"{ev.race_id}-{idx + 1}", result.order,
                         result.times, self.world,
                         stage_type=result.stage_type)

        watch = None
        if self.watch_index is not None:
            watch = {"stage_index": self.watch_index,
                     "mine": [r.rider_id for r in self.roster]}
        outcome = run_event(self.world, event, self.rng, engine="quick",
                            recorder=rec, orders=orders, watch=watch,
                            lineup=(self.team_id, self.lineup)
                                   if self.lineup else None)
        self.lineup = []
        self.last_replay = (watch or {}).get("replay")
        self.watch_index = None

        gc = outcome.classification.gc_standings()
        mine = [(i + 1, self.world.rider(rid))
                for i, (rid, _) in enumerate(gc)
                if self.world.rider(rid).team_id == self.team_id]
        best = mine[0] if mine else None
        winner = self.world.rider(gc[0][0]) if gc else None

        for rid, pts in outcome.ranking_points.items():
            self._points[rid] = self._points.get(rid, 0) + pts

        if winner:
            self._wins[winner.rider_id] = self._wins.get(winner.rider_id, 0) + 1
            if self.db is not None and event.is_stage_race:
                # 总成绩单独记一行（stage_index=0）。单日赛不记——它的
                # 「赛段1」就是它的总成绩，记两遍会让所有统计翻倍。
                record_stage(self.db, self.world.season, event.race_id,
                             event.name, event.tier.value, 0,
                             None, [rid for rid, _ in gc],
                             {rid: t for rid, t in gc}, self.world,
                             stage_type="gc")

        if winner and winner.team_id == self.team_id:
            self.log(f"🏆 {event.name}：{winner.name} 夺冠！", "夺冠")
        elif best:
            self.log(f"{event.name}：冠军 {winner.name}"
                     f"（{self.world.team(winner.team_id).short_name}），"
                     f"我队最好成绩 {best[1].name} 第 {best[0]} 名", "战报")
        self.season_results.append({
            "race": event.name, "tier": event.tier.value,
            "winner": winner.name if winner else "",
            "winner_team": self.world.team(winner.team_id).short_name
                           if winner else "",
            "best_place": best[0] if best else None,
            "best_name": best[1].name if best else "",
        })

        def _row(place, rid, gap):
            r = self.world.rider(rid)
            tm = self.world.team(r.team_id)
            return {"place": place, "rider_id": rid, "name": r.name,
                    "nation": r.nation, "role": r.role.value,
                    "team": tm.short_name, "color": tm.color_primary,
                    "portrait": r.art_portrait, "gap": round(gap, 1),
                    "mine": r.team_id == self.team_id}

        t0 = gc[0][1] if gc else 0.0
        top = [_row(i + 1, rid, tm - t0) for i, (rid, tm) in enumerate(gc[:10])]
        mine_rows = [_row(i + 1, rid, tm - t0)
                     for i, (rid, tm) in enumerate(gc)
                     if self.world.rider(rid).team_id == self.team_id]
        self.last_race = {
            "race": event.name, "race_id": event.race_id,
            "tier": event.tier.value, "days": event.days,
            "stages": [
                {"index": i + 1, "type": st.stage_type,
                 "winner": self.world.rider(st.winner).name if st.winner else "",
                 "winner_mine": bool(st.winner) and
                                self.world.rider(st.winner).team_id == self.team_id}
                for i, st in enumerate(outcome.stages)],
            "top": top, "mine": mine_rows,
            "jerseys": {k: self.world.rider(v).name
                        for k, v in outcome.jerseys.items() if v},
            "playbook": self.playbook,
            "points_gained": sum(
                p for rid, p in outcome.ranking_points.items()
                if self.world.rider(rid).team_id == self.team_id),
        }

        self.race_index += 1

        # 赛后有一定概率触发一件俱乐部事务
        if self.rng.random() < 0.22:
            self._queue_event(When.AFTER_RACE, event.name)
        return self.pending

    def _queue_event(self, when: When, race_name: str = "") -> None:
        ev = pick_event(when, self.rng, self.seen_events)
        if ev is None:
            return
        self.seen_events.add(ev.event_id)
        who = self.rng.choice(self.roster)
        self.pending.append(Decision(
            DecisionKind.EVENT, f"{ev.category.value} · {ev.title}",
            fill(ev.text, who, self.team, race_name),
            [fill(c.label, who, self.team) for c in ev.choices],
            default=0,
            payload={"event_id": ev.event_id, "rider_id": who.rider_id},
        ))

    def _do_offseason(self) -> list[Decision]:
        from game.season import SeasonOutcome

        team_points: dict[str, int] = {}
        for rid, pts in self._points.items():
            tid = self.world.rider(rid).team_id
            team_points[tid] = team_points.get(tid, 0) + pts
        outcome = SeasonOutcome([], dict(self._points), team_points)

        my_pts = team_points.get(self.team_id, 0)
        rank = sorted(team_points.items(), key=lambda x: -x[1])
        my_rank = next((i + 1 for i, (t, _) in enumerate(rank)
                        if t == self.team_id), len(rank))

        wins = sum(1 for r in self.season_results
                   if r["best_place"] == 1)
        self.history.append({
            "season": self.world.season, "rank": my_rank, "points": my_pts,
            "division": self.team.division.value, "wins": wins,
            "budget": self.team.budget,
        })
        self.log(f"{self.world.season} 赛季结束：车队排名第 {my_rank}，"
                 f"积分 {my_pts}，赛事冠军 {wins} 个。", "赛季")

        # 只跑到"合同到期、市场开放"为止，签约留给玩家
        if self.db is not None:
            record_season(self.db, self.world.season, self.world, team_points,
                          self._points, self._race_days, self._wins)

        report = advance_season(self.world, outcome, self.rng, self.team_id,
                                pause_for_transfers=True)
        self._transfers = report["transfers"]
        self._race_days = report.get("race_days", {})
        self._queue_training()

        for line in report["news"][:3]:
            self.log(line, "赞助")
        for c in report["division_changes"]:
            if c.team_id == self.team_id:
                self.log(f"⚠ 本队 {c.old.value} → {c.new.value}", "升降级")
            else:
                self.log(f"{c.team_name.split('-')[0]} "
                         f"{c.old.value} → {c.new.value}", "升降级")

        self.day = 0
        self.phase = Phase.PRESEASON
        self._queue_event(When.OFF_SEASON)
        self._queue_transfer()
        return self.pending

    def play_season(self, auto: bool = True) -> None:
        """跑完一整个赛季。auto=True 时所有待决策取默认值。"""
        start = self.world.season
        guard = 0
        while self.world.season == start and guard < 400:
            guard += 1
            self.advance()
            if self.pending:
                if auto:
                    self.auto_resolve()
                else:
                    return

    # ---- 存档 ----------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": SAVE_VERSION,
            "team_id": self.team_id,
            "seed": self.seed,
            "day": self.day,
            "phase": self.phase.value,
            "race_index": self.race_index,
            "playbook": self.playbook,
            "points": self._points,
            "seen_events": sorted(self.seen_events),
            "headlines": [asdict(h) for h in self.headlines],
            "season_results": self.season_results,
            "history": self.history,
            "pending": [
                {**asdict(d), "kind": d.kind.value} for d in self.pending
            ],
            "world": self.world.to_dict(),
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
        return path

    @staticmethod
    def load(path: str | Path) -> "Career":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("version") != SAVE_VERSION:
            raise ValueError(
                f"存档版本 {raw.get('version')} 与当前 {SAVE_VERSION} 不符")

        # World 的反序列化复用 World.load 的逻辑，先落到临时字典再构造
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(raw["world"], f, ensure_ascii=False)
            tmp = f.name
        world = World.load(tmp)
        Path(tmp).unlink(missing_ok=True)

        c = Career(world, raw["team_id"], raw["seed"])
        c.day = raw["day"]
        c.phase = Phase(raw["phase"])
        c.race_index = raw["race_index"]
        c.playbook = raw["playbook"]
        c._points = raw["points"]
        c.seen_events = set(raw["seen_events"])
        c.headlines = [Headline(**h) for h in raw["headlines"]]
        c.season_results = raw["season_results"]
        c.history = raw["history"]
        c.pending = [
            Decision(DecisionKind(d["kind"]), d["title"], d["detail"],
                     d["options"], d["default"], d["payload"], d["resolved"])
            for d in raw["pending"]
        ]
        return c


def new_career(team_id: str = "T08", seed: int = 2026) -> Career:
    """开一档新生涯。"""
    world = generate(seed)
    return Career(world, team_id, seed)
