"""战术 AI：每一秒，每个车手决定踩多大功率、要不要干活。

设计原则：所有战术都只输出"目标功率"和"是否领骑"两个量，
不直接改位置或名次。谁被甩掉、谁追上来，全部由物理和能量系统涌现。
这样才不会出现"脚本规定他必须赢"的假比赛。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .course import Course, Surface
from .rider import Role


class Mode(str, Enum):
    CRUISE = "cruise"        # 集团里跟着
    PULL = "pull"            # 在前面顶风干活
    ATTACK = "attack"        # 攻击
    CHASE = "chase"          # 追击
    SPRINT = "sprint"        # 冲刺
    LEADOUT = "leadout"      # 带冲刺列车
    SURVIVE = "survive"      # 掉队后自保
    DESCEND = "descend"      # 下坡


@dataclass
class Decision:
    target_power: float
    pulling: bool
    mode: Mode


@dataclass(frozen=True)
class Directive:
    """外部（玩家或车队 AI）对单名车手行为的修正。

    引擎本身不知道"指令"是什么，它只认识这几个乘数。上层怎么把
    "护航队长"翻译成这些数字，是上层的事——这样战术玩法可以随便改，
    不会动到物理和能量。
    """

    pull_bias: float = 1.0      # 愿意领骑的倾向
    attack_bias: float = 1.0    # 攻击触发概率倍率，0 = 不攻击
    spend_bias: float = 1.0     # 舍得烧多少无氧储备保住轮子
    sprint_bias: float = 1.0    # 终盘抢位的积极程度
    early_bias: float = 0.0     # 开场抢突围的额外每秒概率
    conserve: float = 0.0       # 主动降低强度的幅度 0-1


NEUTRAL = Directive()


# --------------------------------------------------------------------------
# 强度基线
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 可调旋钮
#
# 这三个量是耦合的：强度曲线管集团凝聚力，chase_boost 管突围能不能守住，
# tolerated_gap 决定主集团什么时候开始收网。单独动任何一个都会把另外两个
# 的表现推坏——所以它们被摆在一起，由 source/tune_peloton.py 联合拟合，
# 而不是散在代码里各调各的。
# --------------------------------------------------------------------------

# 强度曲线的控制点，(剩余比例, CP 倍数)，按剩余比例从大到小排。
INTENSITY_POINTS: tuple[tuple[float, float], ...] = (
    (0.93, 1.13),   # 开场抢突围，最凶的 15 分钟
    (0.72, 0.92),   # 突围放走后，集团放松
    (0.35, 0.96),
    (0.14, 1.03),
    (0.04, 1.12),
    (0.00, 1.19),   # 最后几公里
)

# True = 控制点之间线性插值（折线）；False = 阶梯。
# 阶梯会把掉队全部堆在跳变的那一秒——一整批人在同一瞬间被推过极限。
INTENSITY_SMOOTH = False

# chase_boost 的总增益。给小了追不回突围，给大了收网期过猛、亚军被拉开。
CHASE_GAIN = 0.85

# tolerated_gap_minutes 的斜率（分钟 / 公里剩余）。
TOLERATED_SLOPE = 0.042


def _intensity_at(remaining_frac: float) -> float:
    """按控制点求强度。阶梯模式取所在区间的值，折线模式做线性插值。"""
    pts = INTENSITY_POINTS
    if remaining_frac > pts[0][0]:
        return pts[0][1]
    if not INTENSITY_SMOOTH:
        for frac, val in pts[1:]:
            if remaining_frac > frac:
                return val
        return pts[-1][1]
    for i in range(len(pts) - 1):
        hi_f, hi_v = pts[i]
        lo_f, lo_v = pts[i + 1]
        if remaining_frac > lo_f:
            span = hi_f - lo_f
            t = (remaining_frac - lo_f) / span if span else 0.0
            return lo_v + (hi_v - lo_v) * t
    return pts[-1][1]


def base_intensity(remaining_frac: float, grade: float, stage_len_km: float) -> float:
    """集团领骑者的基础强度（CP 的倍数）。

    真实比赛的功率曲线不是匀速：开场抢突围很凶，中段"慢慢磨"，
    最后 30 公里再次拉高。这条曲线直接决定了赛段的观赏节奏。
    """
    # 单位是"最前面那个顶风的人花掉的 CP 倍数"。集团里其余人按各自的
    # 破风系数少付，race 层会把这个口径换算成全员平均成本。
    # **这条曲线是阶梯，不是折线，而这是一个已知的取舍。**
    #
    # 跑一场平路赛段测「谁在什么位置掉队」，结果掉队全部堆在赛段的 78%
    # 和 94% 两个点上——那正是两级台阶所在的位置。强度从 0.96 一步跳到
    # 1.03，再跳到 1.12，一整批车手在跳变的那一秒同时被推过极限。
    #
    # 改成在同样的控制点之间线性插值试过了：世巡赛级别集团里一分钟内
    # 完赛的比例从 42% 升到 68%，中位差距从 64 秒降到 23 秒，均速不变
    # （41.2 km/h）——**集团凝聚力确实是被这几级台阶铡掉的。**
    #
    # 但同一次改动让平路赛段的前八变成清一色突围手：节奏拧上去的时候
    # 不再有那一下猛的，收网期的追击就软了，突围守住了。
    # 凝聚力和突围控制在这条曲线上是耦合的，动它必须连 `chase_boost`
    # 一起重调。所以退回阶梯版——**用一个已知的缺陷，换掉一个更糟的缺陷。**
    base = _intensity_at(remaining_frac)

    # 长赛段整体强度更低（保命优先）
    base *= 1.0 - max(0.0, (stage_len_km - 170.0)) * 0.00045

    # 爬坡时集团天然会提速（重力筛人，强度自然更高）
    if grade > 0.03:
        base += min(0.12, (grade - 0.03) * 1.6)
    elif grade < -0.02:
        base -= 0.22         # 下坡不需要那么大功率

    return max(0.45, base)


def selection_factor(remaining_frac: float) -> float:
    """集团配速相对于"集团平均 CP"的倍率。

    这是全引擎最重要的一个旋钮：它决定了什么时候开始筛人。
    开场略低于平均水平（谁都跟得住），越接近终点越高，
    最后一座山按接近队伍最强者的水平跑——弱的人成批掉。
    """
    if remaining_frac > 0.80:
        return 0.96
    if remaining_frac > 0.55:
        return 1.00
    if remaining_frac > 0.30:
        return 1.03
    if remaining_frac > 0.12:
        return 1.05
    if remaining_frac > 0.04:
        return 1.09
    return 1.13


# 主集团愿意放任的差距（分钟），按剩余距离线性收敛到零。
# 这条曲线就是一场公路赛的全部悬念：放到四五分钟，压住，
# 最后四十公里收网。真实转播里屏幕角上跳动的那个数字，就是它。
def tolerated_gap_minutes(remaining_m: float) -> float:
    return max(0.0, min(6.0, remaining_m / 1000.0 * TOLERATED_SLOPE))


def chase_boost(gap_m: float, remaining_m: float, group_size: int,
                commitment: float = 1.0) -> float:
    """主集团的追击强度加成。

    这是一个比例控制器，不是一个固定加成：主集团盯着"当前差距"和
    "此刻该容忍多大差距"之间的误差踩踏板。

    早期版本用的是固定加成，结果差距要么永远为零、要么一路涨到二十五分钟
    还追不回来——因为系统里没有任何东西在管理这个量。加了控制器之后，
    差距会自然收敛到一条像真实比赛那样的曲线，而突围能不能活下来，
    取决于主集团的马力够不够在收网期把误差抹平。

    commitment 是"这个集团今天有多想追"：有冲刺手要保护的队伍多的日子
    追得凶，全是山地手的日子没人愿意干活。
    """
    if gap_m <= 0 or remaining_m <= 0:
        return 0.0
    target_m = tolerated_gap_minutes(remaining_m) * 60.0 * 11.5
    error = gap_m - target_m
    if error <= 0:
        return 0.0                      # 差距在容忍范围内，主集团不动
    # 人力在 45 人处不该封顶。一百六十人的主集团里有七八支队各自带着
    # 冲刺手，愿意上前干活的人是四十五人集团的三四倍——而突围那边人越多
    # 只是轮转更省力，收益远没有这么大。
    #
    # 原来 45 就封顶，造成一个很奇怪的规模阈值：112 人的场地上集团冲刺，
    # 160 人的场地上十个人的突围能守住 26 秒。**集团变大只喂强了突围，
    # 没有喂强追击**，这个不对称是纯粹的模型产物，不是赛车的道理。
    manpower = (0.45 + 0.55 * min(1.0, group_size / 45.0)
                + 0.30 * min(1.0, max(0.0, group_size - 45.0) / 75.0))
    # 误差换算成强度：差一分钟左右就踩满
    drive = min(1.0, error / (60.0 * 11.5))
    # 系数要给得足：真正的收网期主集团能把时速拉到 50 公里。给小了的话
    # 追击速度只比突围快零点几米每秒，八公里的差距要追一整天。
    return CHASE_GAIN * drive * manpower * commitment


# --------------------------------------------------------------------------
# 领骑意愿
# --------------------------------------------------------------------------

def wants_to_pull(state, group, course: Course, rng: random.Random) -> bool:
    """判断该车手这一秒愿不愿意在前面顶风。"""
    r = state.rider
    d = state.directive
    remaining = course.remaining_m(state.distance)

    if state.energy.w_fraction < 0.18:
        return False                       # 没子弹了，谁也不干活
    if state.mode is Mode.SURVIVE:
        return False

    # 指令可以把一个人从"躲风"变成"全程干活"，或者反过来。
    # 用概率而不是硬开关，这样一队八个人的行为仍然是有层次的。
    bias = d.pull_bias
    if bias >= 2.0:
        return rng.random() < min(0.95, 0.55 * bias / 2.4 + 0.35)
    if bias <= 0.2:
        return rng.random() < 0.03

    # 突围小集团：人少，除非明显掉链子，否则都得轮流拉
    if group.size <= 12:
        return state.energy.w_fraction > 0.25

    # 终盘（最后 5 公里）：工兵的活干完了，前排让给冲刺列车。
    # 不让位的话，整场比赛就变成"谁一直在前面顶风谁就赢"。
    if remaining < 5000:
        if r.role is Role.LEADOUT:
            return True
        if r.role in (Role.LEADER, Role.CLIMBER):
            return remaining < 1200 and rng.random() < 0.3
        return False

    # 大集团：工兵和平路手干活，核心和冲刺手躲着
    if r.role in (Role.DOMESTIQUE, Role.ROULEUR):
        return True
    if r.role is Role.LEADOUT:
        return remaining < 9000
    if r.role is Role.SPRINTER:
        return False
    if r.role in (Role.LEADER, Role.CLIMBER):
        return remaining < 2500 and rng.random() < 0.25
    return rng.random() < 0.3


# --------------------------------------------------------------------------
# 攻击判定
# --------------------------------------------------------------------------

def attack_trigger(state, group, course: Course, rng: random.Random,
                   dt: float, groups=None) -> bool:
    """这一秒是否发动攻击。

    攻击是稀疏事件，用"每秒概率"建模而不是硬阈值，
    这样同样的阵容跑两次不会得到完全一样的比赛。
    """
    if state.attack_cooldown > 0 or state.mode is Mode.SURVIVE:
        return False

    r = state.rider
    remaining = course.remaining_m(state.distance)
    grade = course.grade_at(state.distance)
    w = state.energy.w_fraction

    if w < 0.45 or group.size <= 3:
        return False
    if state.draft_rank > max(12, group.size * 0.4):
        return False                       # 埋在集团深处，出不来

    p = 0.0
    if r.role in (Role.LEADER, Role.CLIMBER):
        # 爬坡手只在够陡的坡上动手，且离终点不能太远
        if grade > 0.05 and 1500 < remaining < 28000:
            steep = min(1.0, (grade - 0.05) / 0.05)
            late = min(1.0, (28000 - remaining) / 22000)
            p = 0.00045 * steep * (0.4 + 0.6 * late) * (r.attributes.climbing / 70.0)
    elif r.role is Role.BREAKAWAY:
        # 突围不是在开场那十五分钟形成的——那段时间集团时速五十，
        # 谁也走不掉。真正的逃脱发生在集团松劲之后的那一小时：
        # 主集团把强度降到 0.92，突围的人压着 1.24 跑，差距才拉得开。
        # 早期版本把窗口开在开场，于是攻击一次也不会成功。
        frac = remaining / course.length_m
        if 0.60 < frac < 0.90:
            p = 0.0022 * (r.attributes.resilience / 60.0)
        elif 3000 < remaining < 20000 and group.size < 25:
            p = 0.00025
    elif r.role is Role.ROULEUR:
        seg = course.segment_at(state.distance)
        if seg.surface is Surface.COBBLES or seg.crosswind > 3.0:
            p = 0.0004 * (r.attributes.flat / 70.0)

    # --- 搭桥 ---
    # 单枪匹马永远逃不掉：一个人独走要顶全部的风，而身后是一百多人在轮转，
    # 集团一定更快。突围之所以在现实中成立，是因为有人会跟上去，几个人
    # 轮流破风，把每人的成本摊到七成以下。
    #
    # 少了这一条，引擎里就只会有"冲出去然后被吞掉"的独狼，突围永远不会成功，
    # 中小车队一整个赛季颗粒无收，"抢突围"这条指令也就成了摆设。
    d = state.directive
    if groups:
        ahead = [g for g in groups if g.front_m > group.front_m]
        if ahead:
            nearest = min(ahead, key=lambda g: g.back_m - group.front_m)
            bridge_gap = nearest.back_m - group.front_m
            wants_break = (r.role is Role.BREAKAWAY or d.early_bias > 0
                           or d.attack_bias > 1.5)
            if (wants_break and 15 < bridge_gap < 600
                    and remaining > course.length_m * 0.45 and w > 0.5):
                p = max(p, 0.010 * (1.0 + d.early_bias * 40.0))

    p *= d.attack_bias
    if 0.55 < remaining / course.length_m < 0.92:
        p += d.early_bias                 # 抢突围指令：在逃脱窗口里格外积极

    p *= 0.6 + 0.8 * w
    p *= r.morale
    return rng.random() < p * dt


def breakaway_intensity(remaining_frac: float, size: int) -> float:
    """跑在前面的突围小队的配速强度。

    突围和主集团的区别不在能力，在于**动机**。主集团一天里大部分时间
    在省力气，等最后三十公里再动手；突围的人从上路第一分钟就压着自己的
    极限跑，因为他们只有这一个机会。

    没有这条区分的话，突围永远追不回来也永远逃不掉——引擎里所有集团
    都按同一条配速曲线跑，人多的那一坨永远更快，突围必然在某个时刻被吞掉。
    于是中小车队一整个赛季拿不到任何东西，"抢突围"这条战术指令也就
    彻底失去意义。

    强度随比赛推进而下降：突围的人到后段一定会掉速，这是真实的，
    也是主集团敢于放走他们的原因。
    """
    # 数值来自真实的差距量级反推：突围通常能拿到 3-8 分钟，
    # 对应速度快 3-5%，也就是等效独走功率高 10-20%。
    # 同样是"顶风者 CP 倍数"口径。突围的人一整天都压在自己的临界功率附近，
    # 主集团里的人只用一半——差别不在能力，在于突围只有这一次机会。
    # 换算成每人实际付出后：五人突围每人约 0.98 CP，主集团每人约 0.55 CP。
    # 换算成每人实际付出：三五人的突围里破风折减只有 0.85 左右，
    # 所以 1.10 的"顶风口径"对应每人 0.93 CP——一个人能扛四小时的极限。
    # 给到 1.24 就等于每人 1.05 CP，那是四十分钟就爆掉的强度。
    if remaining_frac > 0.75:
        base = 1.06          # 刚拉开，肾上腺素最足
    elif remaining_frac > 0.40:
        base = 1.02
    elif remaining_frac > 0.18:
        base = 0.97
    else:
        base = 0.88          # 强弩之末，主集团这时候通常已经压上来了
    # 人越多越能轮转，压得住的强度越高
    return base * (0.93 + 0.014 * min(size, 8))


def group_pace_intensity(group, groups, course: Course, distance: float,
                         stage_len_km: float, commitment: float = 1.0) -> float:
    """集团整体配速强度，单位是"集团平均 CP 的倍数"。

    刻意不按某个具体车手的能力来定速：现实中集团的速度是集体行为的结果，
    如果让最强的人定速，全场会在第一座山就被拉爆。
    """
    remaining = course.remaining_m(distance)
    remaining_frac = remaining / course.length_m
    grade = course.grade_at(distance)

    # 是不是"跑在主集团前面的一小撮人"——也就是突围
    main = max(groups, key=lambda g: g.size) if groups else group
    is_break = (group is not main and group.size <= 20
                and group.front_m > main.front_m)

    if is_break:
        intensity = breakaway_intensity(remaining_frac, group.size)
        if grade > 0.03:
            intensity += min(0.10, (grade - 0.03) * 1.4)
        elif grade < -0.02:
            intensity -= 0.22
        return max(0.5, intensity)

    intensity = base_intensity(remaining_frac, grade, stage_len_km)
    intensity *= selection_factor(remaining_frac)

    ahead = [g for g in groups if g.front_m > group.front_m]
    if ahead:
        nearest = min(ahead, key=lambda g: g.back_m - group.front_m)
        intensity += chase_boost(nearest.back_m - group.front_m,
                                 remaining, group.size, commitment)

    # 集团越小配速越保守。这一条同时干两件事：给掉队的小队一个真实的
    # "人少拉不动"惩罚；以及掐掉一个恶性循环——弱者掉队会抬高集团的均值 CP，
    # 均值抬高又让配速更狠、于是掉得更多。不加阻尼的话山顶终点会碾碎全场。
    if group.size < 30:
        intensity *= 0.90 + 0.0033 * group.size

    return intensity


# --------------------------------------------------------------------------
# 主决策
# --------------------------------------------------------------------------

def decide(state, group, groups, course: Course, rng: random.Random,
           dt: float, remaining: float) -> Decision | None:
    """个人决策。

    返回 None 表示"跟集团走"——这一秒他不做任何主动的事，
    功率由 race 层按集团速度和他自己的破风系数反解出来。
    只有主动脱离集体节奏的行为（攻击、冲刺、带线、掉队自保）才返回功率。
    """
    r = state.rider
    e = state.energy
    cp = e.effective_cp()

    # --- 已在攻击中：把攻击打完 ---
    if state.attack_timer > 0:
        return Decision(cp * (1.22 + 0.10 * (r.attributes.resilience / 100.0)),
                        True, Mode.ATTACK)

    # --- 冲刺列车：把队长拉到 400 米处，然后交棒退出，自己不争名次 ---
    if r.role is Role.LEADOUT and group.size > 8 and remaining < 3000:
        if remaining > 400 and e.w_fraction > 0.15:
            return Decision(cp * 1.20, True, Mode.LEADOUT)
        return Decision(cp * 0.80, False, Mode.SURVIVE)

    # --- 掉队自保。缓过一口气就重新参与比赛，否则一次掉队等于判死刑 ---
    if state.mode is Mode.SURVIVE:
        if e.w_fraction > 0.30:
            state.mode = Mode.CRUISE
        else:
            return Decision(cp * r.params.grit * 0.96, True, Mode.SURVIVE)

    # --- 冲刺：终点前拉开的最后一段 ---
    sprint_window = 190.0 + 1.9 * r.attributes.sprint
    if remaining < sprint_window:
        return Decision(e.max_power(r.params.peak_anaerobic), True, Mode.SPRINT)

    # --- 保存体力指令：主动降档，宁可掉出前排也不烧储备 ---
    if state.directive.conserve > 0 and e.w_fraction < 0.55 and remaining > 6000:
        return Decision(cp * (1.0 - state.directive.conserve * 0.35),
                        False, Mode.CRUISE)

    # --- 攻击触发 ---
    if attack_trigger(state, group, course, rng, dt, groups):
        state.attack_timer = rng.uniform(35.0, 85.0)
        state.attack_cooldown = rng.uniform(240.0, 600.0)
        return Decision(cp * 1.28, True, Mode.ATTACK)

    return None


def descent_speed_cap(state, course: Course) -> float:
    """下坡速度上限：既受胆量限制，也受弯道密度限制。"""
    seg = course.segment_at(state.distance)
    base = state.rider.params.max_descent_speed
    skill = state.rider.attributes.descending / 100.0
    penalty = seg.technical * (1.0 - skill) * 0.42
    return base * (1.0 - penalty)
