"""比赛主循环。

每个时间步做四件事：
  1. 按位置把车手聚成集团，算出每个人的破风系数
  2. 定出集团速度（由"集团平均能力 × 强度系数"决定，不由某个人决定）
  3. 每个人反解"跟住这个速度需要多少瓦"——付不起的人自动掉队
  4. 主动行为（攻击、冲刺、带线、掉队自保）绕开集团速度，按自己的功率走

关键设计：名次不是被规则决定的，是被"谁还剩多少 W′"决定的。
掉队、追回、爆掉、终点绝杀，全部从这个循环里涌现，没有一行脚本。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import incidents, pack, physics, tactics
from .course import SURFACE_CRR, Course, StageType, Surface
from .energy import EnergyState
from .rider import Rider, Role
from .tactics import NEUTRAL, Directive, Mode

G = physics.G
ETA = physics.DRIVETRAIN_EFF

PULL_ROTATION_S = 12.0   # 每隔多久重新决定谁在前面干活
ATTACK_CHECK_S = 4.0     # 攻击判定的采样间隔（概率按间隔放大）
INCIDENT_CHECK_S = 5.0   # 意外判定的采样间隔（概率按间隔放大）


@dataclass
class RiderState:
    """一名车手在比赛中的可变状态。"""

    rider: Rider
    energy: EnergyState

    distance: float = 0.0
    speed: float = 8.0          # 实际速度，含集团内挪位置的相对速度
    pace_speed: float = 8.0     # 所在集团的速度，不含挪位置的分量
    elapsed: float = 0.0

    draft: float = 1.0
    draft_rank: int = 0
    group_size: int = 1
    in_echelon: bool = True
    position_rank: int = 0      # 在集团纵深里的位置（与躲不躲风分开）
    pulling: bool = False
    holding: bool = True        # 上一步是否跟住了集团速度
    mode: Mode = Mode.CRUISE
    directive: Directive = NEUTRAL   # 玩家或车队 AI 下达的行为修正

    attack_timer: float = 0.0
    attack_cooldown: float = 0.0

    finished: bool = False
    finish_time: float | None = None
    abandoned: bool = False

    power: float = 0.0
    total_work_j: float = 0.0
    peak_power: float = 0.0
    time_pulling: float = 0.0
    time_dropped: float = 0.0
    attacks_made: int = 0
    min_w_fraction: float = 1.0
    stop_timer: float = 0.0     # 因意外停在路边的剩余秒数
    incidents: list = field(default_factory=list)

    @property
    def avg_power(self) -> float:
        return self.total_work_j / self.elapsed if self.elapsed > 0 else 0.0


@dataclass
class RaceResult:
    course: Course
    finishers: list[RiderState]
    dnf: list[RiderState]
    duration_s: float
    events: list[tuple[float, str]] = field(default_factory=list)
    # 每个爬坡/冲刺点的真实通过顺序：{点的下标: [车手 id, ...]}。
    # 在这之前引擎根本不记录这件事，途中积分只能拿「终点名次加扰动」
    # 去近似——突围里的人先过点，而近似完全看不见这一层。
    kom_order: dict[int, list[str]] = field(default_factory=dict)

    @property
    def winner(self) -> RiderState | None:
        return self.finishers[0] if self.finishers else None

    def gap_to_winner(self, state: RiderState) -> float:
        if not self.finishers or state.finish_time is None:
            return float("inf")
        return state.finish_time - self.finishers[0].finish_time

    def standings(self) -> list[tuple[int, RiderState, float]]:
        return [(i + 1, s, self.gap_to_winner(s)) for i, s in enumerate(self.finishers)]

    def group_gaps(self, threshold_s: float = 1.0) -> list[tuple[int, float]]:
        """把完赛成绩切成"同一集团"的分组，返回 [(人数, 与冠军差距)]。"""
        out: list[tuple[int, float]] = []
        count, anchor = 0, None
        for s in self.finishers:
            t = s.finish_time or 0.0
            if anchor is None or t - anchor <= threshold_s:
                anchor = anchor if anchor is not None else t
                count += 1
            else:
                out.append((count, anchor - self.finishers[0].finish_time))
                anchor, count = t, 1
        if count and anchor is not None:
            out.append((count, anchor - self.finishers[0].finish_time))
        return out


class Race:
    """一场赛段比赛。"""

    def __init__(
        self,
        course: Course,
        riders: list[Rider],
        dt: float = 1.0,
        seed: int | None = None,
        directives: dict[str, Directive] | None = None,
        rain: float = 0.0,               # 0 = 干燥，1 = 大雨
        incidents_on: bool = True,
        max_hours: float = 9.0,
        time_limit_frac: float = 1.16,   # 超过冠军成绩这么多倍即关门
    ) -> None:
        self.course = course
        self.dt = dt
        self.rng = random.Random(seed)
        self.max_steps = int(max_hours * 3600 / dt)
        self.time_limit_frac = time_limit_frac
        self.clock = 0.0
        self.events: list[tuple[float, str]] = []
        # 越点记录。用「上一步在点之前、这一步在点之后」来判定，
        # 所以顺序天然就是真实的通过顺序，不需要额外的排序。
        self.kom_order: dict[int, list[str]] = {i: [] for i in
                                                range(len(course.koms))}
        self._kom_done: dict[int, set] = {i: set() for i in
                                          range(len(course.koms))}
        self._kom_open: list[int] = list(range(len(course.koms)))
        self._kom_closed: set[int] = set()
        self._finishers: list[RiderState] = []
        self._stage_len_km = course.length_m / 1000.0
        # 主集团今天有多想追突围。同样的阵容跑两次，一次放走一次追回，
        # 靠的就是这个数——它是"突围能不能成功"这件事的随机来源，
        # 而不是直接掷骰子决定谁赢。
        self.chase_commitment = self.rng.uniform(0.72, 1.22)
        self.rain = max(0.0, min(1.0, rain))
        self.incidents_on = incidents_on

        self.states: list[RiderState] = []
        for i, r in enumerate(riders):
            st = RiderState(r, r.new_energy_state())
            # 发车时集团有约 200 米纵深，避免所有人挤在同一个点上
            st.distance = -pack.RIDER_SPACING_M * i
            st.speed = 8.0
            if directives:
                st.directive = directives.get(r.rider_id, NEUTRAL)
            self.states.append(st)

    # ---- 内部工具 ------------------------------------------------------

    def _log(self, text: str) -> None:
        self.events.append((self.clock, text))

    # ---- 主循环 --------------------------------------------------------

    def _mark_koms(self) -> None:
        """记录这一步谁越过了爬坡点或冲刺点。

        **通过顺序不是终点名次。** 突围的人先过点、拿走积分，然后被追回，
        终点可能排在五十名开外——爬坡王衫的整个玩法就建立在这个差别上。
        """
        # 每一步扫「所有点 × 所有车手」是 3×160×18000 次比较，实测把
        # verify_incidents 拖到超时。加两道便宜的闸：已经全部通过的点
        # 不再看，还没有人到的点也不再看。
        for i in self._kom_open:
            k = self.course.koms[i]
            done = self._kom_done[i]
            hit = False
            for s in self.states:
                if s.distance < k.distance_m:
                    continue
                if s.abandoned or s.rider.rider_id in done:
                    continue
                done.add(s.rider.rider_id)
                self.kom_order[i].append(s.rider.rider_id)
                hit = True
            if hit and len(done) >= sum(1 for s in self.states
                                        if not s.abandoned):
                self._kom_closed.add(i)
        if self._kom_closed:
            self._kom_open = [i for i in self._kom_open
                              if i not in self._kom_closed]
            self._kom_closed.clear()

    def step(self) -> None:
        dt = self.dt
        c = self.course
        rng = self.rng
        clock = self.clock
        rotate = (clock % PULL_ROTATION_S) < dt
        check_attack = (clock % ATTACK_CHECK_S) < dt
        check_incident = (clock % INCIDENT_CHECK_S) < dt

        # 停在路边换轮或者爬起来的人：这几秒他哪儿也去不了。
        # 放在聚集团之前处理，这样他们会自然地掉出集团。
        for s in self.states:
            if s.stop_timer > 0 and not s.finished and not s.abandoned:
                s.stop_timer -= dt
                s.speed = 0.0
                s.holding = False
                s.mode = Mode.SURVIVE
                s.elapsed += dt

        groups = pack.form_groups(self.states)

        for g in groups:
            members = g.members
            n = len(members)
            # 排序会打乱 members 的位置顺序，先把队首记下来。
            #
            # 关键：拿来当"集团配速基准"的必须是一个**正常跟车**的人。
            # 如果基准取到了正在攻击的那个车手，他一加速，整个集团的速度
            # 积分就跟着他一起往上走——攻击者永远甩不掉身后一百多人，
            # 因为那一百多人是被他自己拖着跑的。这是突围不成立的最后一环。
            head_state = members[0]
            for s in members:
                if s.mode not in (Mode.ATTACK, Mode.SPRINT, Mode.SURVIVE):
                    head_state = s
                    break
            head = members[0].distance
            seg = c.segment_at(head)
            grade = seg.grade
            rho = physics.air_density(c.altitude_at(head))
            remaining_head = c.remaining_m(head)

            theta = math.atan(grade)
            k_grav = G * math.sin(theta)
            k_roll = G * math.cos(theta)
            surface_crr = SURFACE_CRR[seg.surface]

            # --- 1) 轮转：谁在前面干活 ---
            finale = remaining_head < 6000
            if rotate or n <= 8 or finale:
                for s in members:
                    s.pulling = tactics.wants_to_pull(s, g, c, rng)
                members.sort(key=lambda s: (not s.pulling, -s.energy.w_fraction))
            pack.assign_ranks(g, seg.crosswind, finale)

            # --- 2) 集团参考能力（一次 O(n) 遍历） ---
            sum_cp = sum_mass = sum_cda = sum_cda_raw = 0.0
            for s in members:
                # 坡度决定这一步按平路能力还是爬坡能力结算
                s.energy.terrain_mult = s.rider.terrain_cp_mult(grade)
                sum_cp += s.energy.effective_cp()
                sum_mass += s.rider.params.total_mass
                # 用每个人**自己的**破风系数：集团的速度由全员的平均成本
                # 决定，而不是由最前面那个人的成本决定
                sum_cda += s.rider.params.cda_hoods * s.draft
                sum_cda_raw += s.rider.params.cda_hoods
            ref_cp = sum_cp / n
            ref_mass = sum_mass / n
            ref_cda = sum_cda / n          # 全员平均（含各自的破风折减）
            ref_cda_raw = sum_cda_raw / n  # 顶风者口径

            intensity = tactics.group_pace_intensity(
                g, groups, c, head, self._stage_len_km, self.chase_commitment)

            # 集团速度由"全员的平均每人成本"推出：强度 × 平均 CP 的功率，
            # 摊在"平均迎风面积 × 平均破风系数"上。
            #
            # 早期版本让参考骑手站在风里（破风系数固定 1.0），后果是致命的：
            # 一百五十人的集团和一个独走的车手被按同一个成本模型计算，于是
            # 攻击者永远快不过集团，突围一次也不会成功。真实的差别恰恰在这里
            # ——集团里每人只付四成风阻，突围小队每人要付八成，独走要付十成。
            # 这一个系数就同时解释了：为什么集团永远追得回突围、为什么突围
            # 人越多越能活、以及为什么终点前主集团一提速就能吞掉所有人。
            cap = 40.0
            if grade < -0.015:
                base_cap = sum(s.rider.params.max_descent_speed for s in members) / n
                tech = seg.technical
                cap = base_cap * (1.0 - tech * 0.22)

            # 用队首的"集团速度"而不是他的实际速度来积分：实际速度里含有
            # 他刚刚往前挪位置的那一点相对速度，拿来当基准会让集团每一步
            # 都比上一步更快，几步之后配速就失控了。
            # 强度曲线是按"最前面顶风那个人的成本"标定的，而集团速度要用
            # 全员平均成本来解。两者的比值随地形自动变化：平路上风阻占九成，
            # 平均成本只有顶风者的六成；山上重力占九成，跟不跟车几乎没差别，
            # 比值接近一。手填一个固定折算系数是行不通的——试过，
            # 结果是平路刚好、山地赛段直接慢掉七公里每小时。
            v_ref = max(3.0, head_state.pace_speed)
            p_front = physics.power_required(
                v_ref, ref_mass, grade, ref_cda_raw, 0.0042 + surface_crr,
                rho, seg.headwind)
            p_avg = physics.power_required(
                v_ref, ref_mass, grade, ref_cda, 0.0042 + surface_crr,
                rho, seg.headwind)
            shelter_ratio = p_avg / p_front if p_front > 1.0 else 1.0

            group_speed = physics.advance_speed(
                head_state.pace_speed, intensity * ref_cp * shelter_ratio, dt,
                total_mass=ref_mass, grade=grade,
                cda=ref_cda,
                crr=0.0042 + surface_crr, rho=rho,
                headwind=seg.headwind, max_speed=cap,
            )

            # --- 3) 逐人推进 ---
            v = group_speed
            front_new = head + v * dt
            v_air = v + seg.headwind
            aero_term = 0.5 * rho * v_air * abs(v_air)
            v_over_eta = v / ETA

            main_group = max(groups, key=lambda x: x.size)

            for s in members:
                if s.stop_timer > 0:
                    continue                       # 停在路边，这一秒不动
                r = s.rider
                p = r.params
                e = s.energy

                if check_incident and self.incidents_on:
                    self._roll_incident(s, seg, g, main_group, remaining_head)
                    if s.stop_timer > 0 or s.abandoned:
                        continue

                # 个人决策：只有主动行为才返回功率
                if check_attack or s.attack_timer > 0 or remaining_head < 4000 \
                        or s.mode in (Mode.SURVIVE, Mode.ATTACK, Mode.SPRINT):
                    d = tactics.decide(s, g, groups, c, rng, ATTACK_CHECK_S,
                                       c.remaining_m(s.distance))
                else:
                    d = None

                if d is not None:
                    if d.mode is Mode.ATTACK and s.mode is not Mode.ATTACK:
                        s.attacks_made += 1
                        # 起跳：攻击是从集团前几个位置发动的。不这么处理的话，
                        # 攻击者要花掉整段冲刺去穿越几十米的集团，到队首时
                        # 力气也用完了，一米差距都拉不开。
                        s.distance = max(s.distance, head + 2.0)
                        s.draft = 1.0       # 出了集团就得自己顶风
                        s.holding = False
                        if n > 6:
                            self._log(f"{r.name} 在剩余 "
                                      f"{c.remaining_m(s.distance) / 1000:.1f}km 处发动攻击")
                    s.mode = d.mode
                    s.holding = d.mode not in (Mode.SURVIVE, Mode.ATTACK)
                    self._free_ride(s, d.target_power, dt, seg, rho, cap)
                    continue

                # 跟集团：反解需要的功率
                cda = p.cda_hoods * s.draft
                crr = p.crr + surface_crr
                need = (p.total_mass * (k_grav + k_roll * crr) + cda * aero_term) * v_over_eta
                need = max(0.0, need)

                limit = self._hold_limit(s, remaining_head)
                if need <= limit:
                    # 跟得住的人再往集团里"挪位置"：想去的深度由破风排位决定，
                    # 挪得多快由占位属性决定。没有这一步的话，发车时的前后
                    # 顺序会一路保持到终点，冲刺手永远出不来。
                    depth = front_new - (s.distance + v * dt)
                    target = pack.bunch_depth(s.position_rank, n, grade,
                                              seg.crosswind)
                    rate = ((0.35 + 0.55 * r.attributes.positioning / 100.0)
                            * (s.directive.sprint_bias if finale else 1.0))
                    closing = max(-rate, min(rate, (depth - target) * 0.12))
                    s.holding = True
                    s.pace_speed = v
                    s.speed = v + closing
                    s.distance += (v + closing) * dt
                    # 往前挪要多踩一点
                    self._account(s, need * (1.0 + 0.12 * max(0.0, closing) / rate), dt)
                else:
                    s.holding = False
                    self._free_ride(s, limit, dt, seg, rho, cap)
                    s.time_dropped += dt
                    if s.mode is not Mode.SURVIVE and e.w_fraction < 0.02:
                        s.mode = Mode.SURVIVE
                        # 终点前几公里人人都在极限上，此时的掉队没有叙事价值，
                        # 记下来只会把战报刷满噪音
                        if n > 10 and remaining_head > 5000:
                            self._log(f"{r.name} 掉队（剩余 "
                                      f"{c.remaining_m(s.distance) / 1000:.1f}km）")

                if s.pulling:
                    s.time_pulling += dt

        # --- 4) 计时器与终点判定 ---
        for s in self.states:
            if s.finished or s.abandoned:
                continue
            if s.attack_timer > 0:
                s.attack_timer = max(0.0, s.attack_timer - dt)
                if s.attack_timer == 0.0:
                    s.mode = Mode.CRUISE
            if s.attack_cooldown > 0:
                s.attack_cooldown = max(0.0, s.attack_cooldown - dt)
            if s.energy.w_fraction < s.min_w_fraction:
                s.min_w_fraction = s.energy.w_fraction
            if s.distance >= c.length_m:
                overshoot = s.distance - c.length_m
                s.finish_time = self.clock + dt - overshoot / max(s.speed, 0.1)
                s.finished = True
                self._finishers.append(s)

        self._mark_koms()
        self.clock += dt

    def _roll_incident(self, s: RiderState, seg, group, main_group,
                       remaining_head: float) -> None:
        """每 INCIDENT_CHECK_S 秒掷一次意外。概率按采样间隔放大。"""
        rng = self.rng
        a = s.rider.attributes
        surface = seg.surface
        if self.rain > 0.35 and surface is Surface.ASPHALT:
            surface = Surface.WET

        h_crash = incidents.crash_hazard(
            speed=s.speed, grade=seg.grade, surface=surface,
            technical=seg.technical, group_size=group.size,
            draft_rank=s.draft_rank, descending=a.descending,
            w_fraction=s.energy.w_fraction, rain=self.rain,
        ) * INCIDENT_CHECK_S
        h_mech = incidents.mechanical_hazard(
            surface=surface, speed=s.speed, rain=self.rain,
        ) * INCIDENT_CHECK_S

        roll = rng.random()
        if roll < h_crash:
            inc = incidents.roll_crash(
                rng, s.distance,
                self.course.remaining_m(s.distance) / self.course.length_m,
                a.resilience)
        elif roll < h_crash + h_mech:
            inc = incidents.roll_mechanical(rng, s.distance,
                                            group is main_group)
        else:
            return

        s.incidents.append(inc)
        if inc.ends_race:
            s.abandoned = True
            self._log(f"{s.rider.name} 摔车退赛（剩余 "
                      f"{self.course.remaining_m(s.distance) / 1000:.1f}km）")
            return

        s.stop_timer = inc.lost_seconds
        if inc.w_prime_lost:
            s.energy.w_bal = max(0.0, s.energy.w_bal
                                 * (1.0 - inc.w_prime_lost))
        if group.size > 6:
            self._log(f"{s.rider.name} {inc.label}，损失 "
                      f"{inc.lost_seconds:.0f} 秒（剩余 "
                      f"{self.course.remaining_m(s.distance) / 1000:.1f}km）")

    # ---- 推进与记账 ----------------------------------------------------

    def _free_ride(self, s: RiderState, power: float, dt: float,
                   seg, rho: float, cap: float) -> None:
        """不跟集团速度，按自己的功率走。"""
        p = s.rider.params
        if s.mode is Mode.DESCEND or (seg.grade < -0.03 and s.mode is not Mode.SPRINT):
            cda = p.cda_aero * s.draft
            cap = min(cap, tactics.descent_speed_cap(s, self.course))
        elif s.mode is Mode.SPRINT:
            # 冲刺时不再沿用集团里的破风系数：最后 200 米每个人都得自己出来
            # 迎风。埋在集团第 60 位不是优势而是劣势，能不能找到好轮子由占位
            # 属性决定。不这么处理的话，"躲在最深处"会变成最优解。
            cda = p.cda_hoods * 0.94 * (0.90 - 0.0012 * s.rider.attributes.positioning)
        else:
            cda = p.cda_hoods * s.draft

        s.speed = physics.advance_speed(
            s.speed, power, dt,
            total_mass=p.total_mass, grade=seg.grade, cda=cda,
            crr=p.crr + SURFACE_CRR[seg.surface], rho=rho,
            headwind=seg.headwind, max_speed=cap,
        )
        s.pace_speed = s.speed
        s.distance += s.speed * dt
        self._account(s, power, dt)

    def _account(self, s: RiderState, power: float, dt: float) -> None:
        s.power = power
        s.total_work_j += power * dt
        if power > s.peak_power:
            s.peak_power = power
        s.energy.update(power, dt)
        s.elapsed += dt

    def _hold_limit(self, s: RiderState, remaining: float) -> float:
        """为了不掉队，这一秒最多愿意输出多少功率。

        不是生理极限，而是"愿意烧掉多少火柴"。离终点越近，越舍得。
        """
        e = s.energy
        cp = e.effective_cp()
        if e.w_bal <= 0:
            # 火柴烧光了。剩下的只有意志——这就是"爆掉"的那一刻
            return cp * s.rider.params.grit

        # 只要还有储备，车手一定会掏出来保住轮子：宁可后面爆掉，
        # 也不会主动松开。掉队因此永远是"没子弹了"的结果，而不是选择。
        # 分母 45 秒 = 愿意为一次变速烧掉多长时间的储备。
        return min(cp + e.w_bal / 45.0 * s.directive.spend_bias,
                   e.max_power(s.rider.params.peak_anaerobic))

    # ---- 个人计时赛 ----------------------------------------------------

    def _run_itt(self) -> RaceResult:
        """个人计时赛：逐个发车，没有集团也没有跟车。

        单独一条路径而不是复用主循环，因为 ITT 里几乎所有集团逻辑都不成立，
        硬套过去只会得到"一群人抱团骑计时赛"这种荒唐结果。
        """
        dt = self.dt
        c = self.course
        for s in self.states:
            r = s.rider
            p = r.params
            e = s.energy
            s.distance = 0.0
            s.speed = 8.0
            s.mode = Mode.PULL
            # 计时赛配速：能力越强越敢压在 CP 之上，一路稳住
            pacing = 0.98 + 0.0011 * r.attributes.time_trial
            t = 0.0
            while s.distance < c.length_m and t < 4 * 3600:
                seg = c.segment_at(s.distance)
                e.terrain_mult = r.terrain_cp_mult(seg.grade)
                remaining_frac = c.remaining_m(s.distance) / c.length_m
                cp = e.effective_cp()
                # 终点前把剩余无氧储备全部倒出来
                push = pacing + (0.28 * (1.0 - remaining_frac) ** 4)
                power = min(cp * push, e.sustainable_for(max(30.0, remaining_frac * 3000)))
                if e.w_bal <= 0:
                    power = min(power, cp * p.grit)
                cap = 40.0
                if seg.grade < -0.015:
                    cap = tactics.descent_speed_cap(s, c)
                cda = p.cda_aero if seg.grade < 0.055 else p.cda_hoods
                s.speed = physics.advance_speed(
                    s.speed, power, dt, total_mass=p.total_mass, grade=seg.grade,
                    cda=cda, crr=p.crr + SURFACE_CRR[seg.surface],
                    rho=physics.air_density(c.altitude_at(s.distance)),
                    headwind=seg.headwind, max_speed=cap,
                )
                s.distance += s.speed * dt
                self._account(s, power, dt)
                t += dt
            overshoot = s.distance - c.length_m
            s.finish_time = t - overshoot / max(s.speed, 0.1)
            s.finished = True
            self._finishers.append(s)

        self._finishers.sort(key=lambda s: s.finish_time or 1e9)
        self._log(f"{self._finishers[0].rider.name} 赢得 {c.name}")
        return RaceResult(c, list(self._finishers), [], self._finishers[-1].finish_time or 0.0,
                          self.events)

    # ---- 运行 ----------------------------------------------------------

    def run(self) -> RaceResult:
        if self.course.stage_type is StageType.ITT:
            return self._run_itt()

        cutoff: float | None = None
        for _ in range(self.max_steps):
            if all(s.finished or s.abandoned for s in self.states):
                break
            self.step()
            if cutoff is None and self._finishers:
                cutoff = (self._finishers[0].finish_time or 0.0) * self.time_limit_frac
            if cutoff is not None and self.clock > cutoff:
                for s in self.states:
                    if not s.finished:
                        s.abandoned = True
                break

        self._finishers.sort(key=lambda s: s.finish_time or 1e9)
        if self._finishers:
            self._log(f"{self._finishers[0].rider.name} 赢得 {self.course.name}")
        return RaceResult(
            course=self.course,
            finishers=list(self._finishers),
            dnf=[s for s in self.states if not s.finished],
            duration_s=self.clock,
            events=self.events,
            kom_order={i: list(v) for i, v in self.kom_order.items()},
        )


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def format_gap(seconds: float) -> str:
    if seconds < 0.05:
        return "—"
    m = int(seconds // 60)
    s = seconds % 60
    return f"+{m}:{s:04.1f}" if m else f"+{s:.1f}s"
