"""集团与破风。

自行车比赛的全部战术张力都来自一件事：跟在别人后面能省 25-45% 的功率。
这个模块负责两件事：
  1. 把散布在赛道上的车手聚成"集团"（gap 小于阈值就算同一集团）
  2. 给集团内每个人算出破风折减系数

破风折减不是位置的函数那么简单——集团越大，中后段的遮蔽越好；
侧风会把长队列吹散成扇形（echelon），超出容量的人被甩在风里。
"""

from __future__ import annotations

from dataclasses import dataclass

# 判定为同一集团的最大间隔（米）。约等于 1.5 秒车距
GROUP_GAP_M = 22.0

# 同一时刻真正暴露在风里的人数上限
FRONT_WORKERS = 10

# 上一步还跟得住集团速度的车手，允许更大的间隔仍算同一集团。
# 没有这条，集团内部正常的挪位置（有人往前有人往后）会被误判成"分裂"，
# 大集团会在平路上凭空炸成两半。
HOLDING_GAP_M = 70.0

# 破风系数：按在集团中的排位（0 = 最前面顶风）
# 数值取自风洞与现场功率计研究的中位区间
_DRAFT_BY_RANK = [1.00, 0.74, 0.69, 0.66, 0.645, 0.635, 0.628, 0.622]
_DEEP_BUNCH_DRAFT = 0.575   # 大集团内部，四面都是人


def draft_factor(rank: int, group_size: int, crosswind: float = 0.0,
                 draft_skill: float = 1.0) -> float:
    """返回 CdA 的乘数（1.0 = 完全顶风）。

    rank: 在集团中的位置，0 为最前。
    group_size: 集团人数，越大遮蔽越好。
    crosswind: 侧风 m/s，削弱直线跟车的效果。
    draft_skill: 占位属性带来的修正，好车手能贴得更近、更省。
    """
    if rank <= 0:
        return 1.0

    if rank < len(_DRAFT_BY_RANK):
        base = _DRAFT_BY_RANK[rank]
    else:
        base = _DEEP_BUNCH_DRAFT

    # 大集团额外遮蔽：50 人以上的主集团比 8 人小队再省一点
    if group_size >= 30 and rank >= 8:
        base = min(base, _DEEP_BUNCH_DRAFT)
    elif group_size < 6:
        base += 0.03 * (6 - group_size)  # 人少，轮转间隙顶风时间长

    # 侧风：跟车收益按侧风强度线性衰减，8 m/s 以上基本无收益
    if crosswind > 0:
        shelter = 1.0 - base
        base = 1.0 - shelter * max(0.0, 1.0 - crosswind / 8.0)

    # 占位技术：只影响"省下来的那部分"，不会让人省得比物理极限更多
    shelter = (1.0 - base) * draft_skill
    return max(0.50, 1.0 - shelter)


def echelon_capacity(crosswind: float, road_width_factor: float = 1.0) -> int:
    """侧风下一个扇形队列能容纳的人数。超出的人只能挂在风里。

    无侧风时返回一个很大的数（不限制）。侧风越强，扇形越短。
    """
    if crosswind < 2.5:
        return 10_000
    cap = int(round(28.0 * road_width_factor / (crosswind / 2.5)))
    return max(6, cap)


@dataclass
class Group:
    """赛道上的一个集团。"""

    members: list  # list[RiderState]，按位置从前到后排序
    front_m: float
    back_m: float

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def lead_distance(self) -> float:
        return self.front_m

    def gap_to(self, other: "Group") -> float:
        """本集团头车落后于 other 尾车多少米（正数表示在后面）。"""
        return other.back_m - self.front_m


def form_groups(states: list, gap_m: float = GROUP_GAP_M) -> list[Group]:
    """按赛道位置把车手聚类成集团。

    states 需要有 .distance 属性且未退赛。返回按领先顺序排列的集团列表。
    """
    active = [s for s in states if not s.finished and not s.abandoned]
    if not active:
        return []

    active.sort(key=lambda s: -s.distance)

    groups: list[Group] = []
    current = [active[0]]
    for s in active[1:]:
        # 宽松阈值只在"前后两个人都在正常跟车"时才用。只看后面那个人的话，
        # 一个正在攻击、已经拉开五十米的车手，会因为身后的集团"跟得很正常"
        # 而被判定成还在集团里——突围于是永远不会被识别出来。
        prev = current[-1]
        both_cruising = s.holding and prev.holding
        # 掉队的人会自动抱团成"关门集团"一起往回骑，这在现实里是常态。
        # 不给他们这个宽容度的话，后半程会碎成几十个孤零零的个体，
        # 每个都因为"人少拉不动"而更慢，最后被关门刷掉四分之一的车手。
        both_dropped = (s.mode.value == "survive" and prev.mode.value == "survive")
        tolerance = HOLDING_GAP_M if (both_cruising or both_dropped) else gap_m
        if prev.distance - s.distance <= tolerance:
            current.append(s)
        else:
            groups.append(Group(current, current[0].distance, current[-1].distance))
            current = [s]
    groups.append(Group(current, current[0].distance, current[-1].distance))
    return groups


# 集团中相邻"排"的纵向间距（米）
ROW_SPACING_M = 2.2


def riders_abreast(size: int, grade: float, crosswind: float) -> int:
    """集团在路上并排几个人。

    这件事看着琐碎，其实决定了终点前的一切：宽路上大集团是 5-6 人一排、
    全长只有几十米，冲刺手离最前面不过十来米，一蹬就能出来；一旦上坡或者
    刮侧风，队形被拉成一长条，排在后面就是排在几百米之后，再快也没用。
    """
    if size < 8:
        return 1
    if crosswind > 3.0 or grade > 0.04:
        return 1          # 侧风或爬坡：拉成单列长龙
    if grade > 0.02:
        return 2
    return 5              # 宽路巡航：挤成一大坨


def bunch_depth(rank: int, size: int, grade: float, crosswind: float) -> float:
    """排位 rank 的车手，落后集团最前端多少米。"""
    return ROW_SPACING_M * (rank // riders_abreast(size, grade, crosswind))


# 兼容旧名字
RIDER_SPACING_M = ROW_SPACING_M

# 终盘抢位时的角色优先级：数字越小越靠前
_FINALE_PRIORITY = {
    "leadout": 0, "sprinter": 1, "leader": 2, "climber": 3,
    "rouleur": 4, "breakaway": 5, "domestique": 6,
}


def _attack_intent(state) -> float:
    """这名车手有多想找机会出去。用来决定他会不会主动占在集团前部。"""
    d = getattr(state, "directive", None)
    intent = 0.0
    if state.rider.role.value in ("breakaway", "climber", "leader"):
        intent += 0.6
    if d is not None:
        if d.attack_bias > 1.5:
            intent += 1.0
        if d.early_bias > 0:
            intent += 1.0
        if d.conserve > 0:
            intent -= 0.8
    return intent


def assign_ranks(group: Group, crosswind: float = 0.0,
                 finale: bool = False) -> None:
    """给集团内每人分配破风排位，写回 state.draft_rank / state.draft。

    排位规则：愿意干活的人（pulling=True）轮流在前，其余按"占位属性 +
    战术意图"排在后面。这比按物理位置排更符合实际——集团里的位置
    是不断轮换的，重要的是"这一秒你在不在风里"。
    """
    members = group.members
    if not members:
        return

    # 真正暴露在风里的只有最前面一小队人。一个 150 人的集团里"愿意干活"的
    # 可能有五十个，但路面就那么宽，同一时刻站在风里的不会超过十来个。
    #
    # 这条限制看着琐碎，却是突围能不能存在的前提：不加限制的话，不干活的人
    # 一律被压到五十名开外，而攻击判定要求你在集团前部——结果**唯一想突围
    # 的那批人永远没资格发起突围**，整个赛季一次成功的逃脱都不会出现。
    willing = [s for s in members if s.pulling]
    workers = willing[:FRONT_WORKERS]
    riders = willing[FRONT_WORKERS:] + [s for s in members if not s.pulling]

    # 没人愿意领骑时，集团里最靠前的那个被迫顶风（现实中队伍会僵住降速）
    if not workers:
        workers = [members[0]]
        riders = members[1:]

    # 不干活的人里，占位好的排前面（前排安全、少受风），差的被挤到最后。
    # 终盘则按角色抢位：冲刺列车和冲刺手挤到最前，工兵被推到后面。
    #
    # 玩家指令在这里第一次真正起作用：被要求"冲刺争胜"的人会插到本角色
    # 该有的位置之前，被要求"保存体力"的人主动往后退。只改"挪位置的速度"
    # 是不够的——终盘几公里足够所有人各就各位，真正决定胜负的是
    # **想站到哪儿**，不是**挪得多快**。
    if finale:
        riders.sort(key=lambda s: (-round(s.directive.sprint_bias, 2),
                                   _FINALE_PRIORITY.get(s.rider.role.value, 9),
                                   -s.rider.params.draft_skill, s.rider.rider_id))
    else:
        # 想突围的人会主动往集团前部顶，好在机会出现时能跟得上
        riders.sort(key=lambda s: (-_attack_intent(s),
                                   -s.rider.params.draft_skill, s.rider.rider_id))

    # 两套排位，刻意分开：
    #   draft_rank —— 这一秒谁躲在风里。前排只站得下十来个人。
    #   position_rank —— 你在集团纵深的哪个位置。这个跟"愿不愿意干活"
    #                    强相关，而且必须稳定，否则每次轮转都会让几十个人
    #                    在路上前后蹿动，集团会被误判成不断分裂。
    #
    # 早期版本用同一个排位表达两件事，于是"限制前排人数"这个正确的改动
    # 顺带把几十个人的物理位置也搅乱了，山地赛段完赛率从 89% 掉到 75%。
    ordered = workers + riders
    # 纵深位置必须用一个**稳定**的顺序。用体力剩余量来排会让几十个人
    # 每十几秒就在路上前后蹿动，集团会被反复误判成分裂——山地赛段的
    # 完赛率会从 89% 掉到 72%。占位属性是稳定的，正好合适。
    ordered_pos = willing + sorted(
        (s for s in members if not s.pulling),
        key=lambda s: (-s.rider.params.draft_skill, s.rider.rider_id))
    size = len(ordered)
    cap = echelon_capacity(crosswind)

    for pos, s in enumerate(ordered_pos):
        s.position_rank = pos

    for rank, s in enumerate(ordered):
        if rank >= cap:
            # 挤出扇形，暴露在风里，只剩一点点遮蔽
            s.draft = min(0.93, draft_factor(1, size, crosswind,
                                             s.rider.params.draft_skill) + 0.22)
            s.in_echelon = False
        else:
            s.draft = draft_factor(rank, size, crosswind,
                                   s.rider.params.draft_skill)
            s.in_echelon = True
        s.draft_rank = rank
        s.group_size = size
