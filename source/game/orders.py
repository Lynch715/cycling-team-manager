"""赛前指令：把玩家的意志接进物理引擎。

这是整个项目里最关键的一层——在它之前，所有东西都是 AI 自己在跑，
玩家不存在。有了它，"我这场想怎么打"才会真的改变比赛结果。

设计上有一条硬规矩：**指令不直接改成绩，只改行为**。

一条"山地进攻"指令做的是提高攻击触发概率、让车手更舍得烧无氧储备，
而不是给他加百分之几的速度。攻击成不成功仍然由物理和能量决定——
下错指令会真的输，下对指令也可能因为对手更强而输。

一旦允许指令直接加成绩，玩家会立刻发现最优解是"永远选最激进的那个"，
战术层就退化成一个装饰性的下拉菜单。
"""

from __future__ import annotations

from enum import Enum

from sim.rider import Role
from sim.tactics import Directive


class Order(str, Enum):
    """给单个车手的赛前指令。"""

    PROTECT = "护航队长"      # 全程为队长挡风、递水、拖回掉队
    FREE = "自由发挥"         # 按角色本能行动，引擎默认行为
    BREAKAWAY = "抢突围"      # 开场就往前冲，赌一次远距离逃脱
    SPRINT = "冲刺争胜"       # 全程躲风保存体力，终点前争名次
    LEADOUT = "带冲刺列车"    # 最后 3 公里把队长拉到前排后退出
    ATTACK_CLIMB = "山地进攻"  # 在陡坡上寻找机会攻击
    CONSERVE = "保存体力"      # 尽量少花力气，为后面的赛段留着
    DOMESTIQUE = "全程干活"    # 领骑、控制集团节奏


# 每条指令翻译成引擎的行为旋钮。
# 这张表就是"战术"在数值上的全部含义，改战术平衡只需要改这里。
EFFECTS: dict[Order, Directive] = {
    Order.PROTECT:      Directive(pull_bias=2.4, attack_bias=0.0,
                                  spend_bias=1.15, sprint_bias=0.5),
    Order.FREE:         Directive(),
    Order.BREAKAWAY:    Directive(pull_bias=0.4, attack_bias=3.5,
                                  spend_bias=1.10, early_bias=0.010,
                                  sprint_bias=0.7),
    Order.SPRINT:       Directive(pull_bias=0.05, attack_bias=0.0,
                                  spend_bias=1.25, sprint_bias=1.6,
                                  conserve=0.10),
    Order.LEADOUT:      Directive(pull_bias=1.6, attack_bias=0.0,
                                  spend_bias=1.10, sprint_bias=1.3),
    Order.ATTACK_CLIMB: Directive(pull_bias=0.3, attack_bias=2.8,
                                  spend_bias=1.30, sprint_bias=0.8),
    Order.CONSERVE:     Directive(pull_bias=0.05, attack_bias=0.0,
                                  spend_bias=0.78, sprint_bias=0.4,
                                  conserve=0.22),
    Order.DOMESTIQUE:   Directive(pull_bias=3.0, attack_bias=0.15,
                                  spend_bias=1.05, sprint_bias=0.6),
}

# 没有指令时，按角色给一个合理的默认值。
# 玩家不管的车手不会变成呆子，这样玩家可以只操心关键的两三个人。
#
# 全能型原来的默认是「全程干活」。那个默认在完整引擎里跑单场没问题——
# 一个全能型确实经常在前面领骑——但把它当成全世界所有全能型每一天的
# 默认，等于宣布这个角色永远不赢：25 个赛季跑下来，全能型的 per100 从
# 113 掉到 38，丘陵赛段的冠军全被冲刺手接手了。**默认值不是「典型行为」，
# 是「没人管他的时候他为自己做什么」。** 一个没被指派工作的全能型
# 会为自己骑。
#
# 总成绩核心默认「保存体力」而不是「自由发挥」：这样他才会被队友掩护，
# 否则整个掩护机制在 AI 车队里永远不触发——没有人是被保护的那一个。
DEFAULT_BY_ROLE = {
    Role.LEADER: Order.CONSERVE,
    Role.CLIMBER: Order.ATTACK_CLIMB,
    Role.SPRINTER: Order.SPRINT,
    Role.LEADOUT: Order.LEADOUT,
    Role.ROULEUR: Order.FREE,
    Role.BREAKAWAY: Order.BREAKAWAY,
    Role.DOMESTIQUE: Order.PROTECT,
}


def effect_for(order: Order | None, role: Role) -> Directive:
    if order is None:
        order = DEFAULT_BY_ROLE.get(role, Order.FREE)
    return EFFECTS[order]


def build_directives(riders, orders: dict[str, Order] | None = None
                     ) -> dict[str, Directive]:
    """把一份 {车手 id: 指令} 翻译成引擎认识的旋钮。

    没给指令的车手按角色取默认值——玩家只需要操心关键的两三个人，
    其余的会照本能行动，而不是变成呆子。
    """
    orders = orders or {}
    return {r.rider_id: effect_for(orders.get(r.rider_id), r.role)
            for r in riders}


# --------------------------------------------------------------------------
# 阵容级预设：玩家常用的整队打法，一键铺开
# --------------------------------------------------------------------------

PLAYBOOKS: dict[str, dict[Role, Order]] = {
    "总成绩优先": {
        Role.LEADER: Order.CONSERVE, Role.CLIMBER: Order.PROTECT,
        Role.SPRINTER: Order.CONSERVE, Role.LEADOUT: Order.PROTECT,
        Role.ROULEUR: Order.PROTECT, Role.BREAKAWAY: Order.PROTECT,
        Role.DOMESTIQUE: Order.PROTECT,
    },
    "冲刺夺段": {
        Role.LEADER: Order.CONSERVE, Role.CLIMBER: Order.CONSERVE,
        Role.SPRINTER: Order.SPRINT, Role.LEADOUT: Order.LEADOUT,
        Role.ROULEUR: Order.DOMESTIQUE, Role.BREAKAWAY: Order.DOMESTIQUE,
        Role.DOMESTIQUE: Order.DOMESTIQUE,
    },
    "全员抢突围": {
        Role.LEADER: Order.FREE, Role.CLIMBER: Order.BREAKAWAY,
        Role.SPRINTER: Order.CONSERVE, Role.LEADOUT: Order.BREAKAWAY,
        Role.ROULEUR: Order.BREAKAWAY, Role.BREAKAWAY: Order.BREAKAWAY,
        Role.DOMESTIQUE: Order.BREAKAWAY,
    },
    "山地强攻": {
        Role.LEADER: Order.ATTACK_CLIMB, Role.CLIMBER: Order.ATTACK_CLIMB,
        Role.SPRINTER: Order.CONSERVE, Role.LEADOUT: Order.PROTECT,
        Role.ROULEUR: Order.PROTECT, Role.BREAKAWAY: Order.BREAKAWAY,
        Role.DOMESTIQUE: Order.PROTECT,
    },
    "保存实力": {r: Order.CONSERVE for r in Role},
}


def apply_playbook(name: str, riders) -> dict[str, Order]:
    """按预设给一队人铺指令。riders 需要有 .rider_id 和 .role。"""
    book = PLAYBOOKS[name]
    return {r.rider_id: book.get(r.role, Order.FREE) for r in riders}
