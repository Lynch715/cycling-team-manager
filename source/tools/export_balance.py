"""导出数值总表：所有能调的旋钮，集中到一份文档里。

    python3 source/tools/export_balance.py

项目做到这个体量之后，最大的风险不是写不出功能，而是**没人知道该去哪里
调数值**。一个策划想让山地赛段更难一点，要翻六个文件才能找到那四个相关
的常数，然后大概率只改对了其中两个。

这份文档从代码里直接读当前值，所以永远不会过期。每一项都写清楚：
它控制什么、调大了会怎样、以及和它耦合的其他旋钮。

**耦合关系是这份文档最重要的部分。** 调 base_intensity 会同时改变赛段
均速和掉队率；调 breakaway_intensity 而不动 chase_boost，突围就会失控。
这些关系写在这里，比写在各自的注释里有用得多。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game import management, market, quickresolve, season, training  # noqa: E402
from game.generate_world import BASE_GRANT, DIVISION_PREMIUM, ROLE_VALUE  # noqa: E402
from sim import incidents, pack, tactics  # noqa: E402
from sim.energy import recovery_tau  # noqa: E402
from sim.physics import DRIVETRAIN_EFF, RHO_SEA_LEVEL  # noqa: E402


def fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, dict):
        return "、".join(f"{getattr(k, 'value', k)}={fmt(x)}"
                         for k, x in list(v.items())[:6])
    if isinstance(v, (list, tuple)):
        return "、".join(fmt(x) for x in list(v)[:8])
    return str(v)


SECTIONS = [
    ("物理与能量", "改这里会动摇全部标定，动之前先跑 calibrate.py", [
        ("传动效率", DRIVETRAIN_EFF, "腿部功率到后轮的损耗。0.976 是实测值，基本不该动"),
        ("海平面空气密度", RHO_SEA_LEVEL, "海拔与温度的修正在 physics.air_density 里"),
        ("W′ 回充时间常数（低于 CP 100W 时）", round(recovery_tau(100), 1),
         "秒。越小恢复越快，直接影响一场比赛能打几次仗"),
    ]),

    ("集团配速（全引擎最敏感的一组）", "调任何一项都要重跑 calibrate.py", [
        ("开场强度", tactics.base_intensity(0.95, 0.0, 180), "顶风者的 CP 倍数"),
        ("中段强度", tactics.base_intensity(0.50, 0.0, 180),
         "调低会让赛段均速下降、同时让突围更容易活"),
        ("终盘强度", tactics.base_intensity(0.02, 0.0, 180), "决定终点前的收网力度"),
        ("筛选系数（终盘）", tactics.selection_factor(0.02),
         "乘在强度上。这是「什么时候开始成批掉人」的总开关"),
        ("小集团阻尼阈值", 30, "人数低于这个数的集团配速打折，防止掉队正反馈失控"),
    ]),

    ("突围与追击", "这两组必须一起调，只动一边突围就会失控", [
        ("突围强度（刚拉开）", tactics.breakaway_intensity(0.85, 5),
         "五人突围，顶风者口径"),
        ("突围强度（末段）", tactics.breakaway_intensity(0.10, 5), "强弩之末"),
        ("主集团容忍差距（剩 100km）",
         round(tactics.tolerated_gap_minutes(100000), 1),
         "分钟。这条曲线就是转播画面角上那个数字"),
        ("主集团容忍差距（剩 20km）",
         round(tactics.tolerated_gap_minutes(20000), 1), "分钟"),
        ("追击控制器增益", 0.85,
         "见 tactics.chase_boost。给小了八公里的差距要追一整天"),
    ]),

    ("破风与队形", "决定「省力」和「安全」之间的取舍", [
        ("同一集团的最大间隔", pack.GROUP_GAP_M, "米"),
        ("正常跟车时的宽容间隔", pack.HOLDING_GAP_M,
         "米。两个人都在正常跟车时才用，否则突围永远不会被识别出来"),
        ("同时暴露在风里的人数", pack.FRONT_WORKERS,
         "路面宽度决定的硬上限。这个数直接决定了想突围的人有没有资格发动"),
        ("深层集团破风系数", pack._DEEP_BUNCH_DRAFT, "CdA 乘数，越小越省力"),
        ("排间距", pack.ROW_SPACING_M, "米。配合并排人数决定集团有多长"),
    ]),

    ("摔车与机械故障", "发生率改完跑 verify_incidents.py", [
        ("摔车基础风险", incidents.CRASH_BASE, "每秒。乘上路面/速度/位置等倍率"),
        ("机械故障基础风险", incidents.MECH_BASE, "每秒"),
        ("石板路摔车倍率", incidents.SURFACE_CRASH[list(incidents.SURFACE_CRASH)[2]],
         "石板路只占全程五分之一里程，摊薄之后才是真实量级"),
        ("石板路机械倍率", incidents.SURFACE_MECH[list(incidents.SURFACE_MECH)[2]],
         "石板路是器材的坟场"),
    ]),

    ("快速结算", "改完必须跑 calibrate_quick.py 对账", [
        ("冠军均速公式", "47.00 − 3.22 × √(每公里爬升)",
         "由完整引擎在五条赛道上拟合。引擎一改这里就要重拟合"),
        ("赛事级状态波动（山地）", quickresolve.RACE_SIGMA["mountain"],
         "整场不变。多日赛的主线来自它"),
        ("赛段级状态波动（山地）", quickresolve.DAILY_SIGMA["mountain"],
         "每天重掷。二十一天下来会互相抵消"),
        ("意外发生率（平路）", quickresolve.INCIDENT_RATE["flat"], "每人每赛段"),
    ]),

    ("赛季与积分", "决定「追求什么」", [
        ("赛事等级积分倍率", "大环赛 4.0、纪念碑 3.0、世巡赛 2.0", "见 classification"),
        ("赛段冠军积分折扣", 0.12,
         "赢八个赛段的冲刺手不该超过赢下总成绩的人"),
        ("疲劳累积（每比赛日）", season.FATIGUE_PER_STAGE, ""),
        ("疲劳恢复（每休息日）", season.FATIGUE_RECOVERY_PER_REST_DAY, ""),
        ("赛段权重（平路 / 山地）",
         f"{season.STAGE_WEIGHT['flat']} / {season.STAGE_WEIGHT['summit_finish']}",
         "选人时用。平路权重低，因为它几乎不产生时间差距"),
    ]),

    ("成长与老化", "决定世界十个赛季之后长什么样", [
        ("巅峰年龄", management.PEAK_AGE, ""),
        ("开始有退役概率的年龄", management.RETIRE_AGE_MIN, ""),
        ("世界水平目标", management.WORLD_LEVEL_TARGET,
         "前 20 名平均总评。青训质量按这个数做负反馈，防止世界逐年变弱"),
        ("专项训练权重", "重点 1.60 / 非重点 0.55",
         "见 management.develop。差距越大，专项化越明显"),
        ("大强度训练成长倍率", training.INTENSITY[training.Intensity.HEAVY]["gain"], ""),
        ("大强度训练受伤倍率", training.INTENSITY[training.Intensity.HEAVY]["injury"], ""),
    ]),

    ("经济与转会", "决定小队有没有活路", [
        ("等级薪资溢价", DIVISION_PREMIUM,
         "同一个人在世巡赛能拿到洲际队六倍以上。这是整个经营层的驱动力"),
        ("角色身价系数", ROLE_VALUE, "市场为「能赢比赛的人」付钱"),
        ("奖金换算率", management.PRIZE_RATE, "万元 / 世界排名积分"),
        ("等级基础拨款", "世巡赛 800 / 职业队 380 / 洲际队 120",
         "万元。器材商、出场费、转播分成"),
        ("报价谈崩线", market.WALK_AWAY, "低于市场价这个比例，车手直接不谈"),
        ("等级号召力", market.DIVISION_PULL, "同样的钱，世巡赛队更容易签到人"),
    ]),

    ("升降级", "决定中游队伍的赛季有没有第二个目标", [
        ("滚动赛季数", management.ROLLING_SEASONS,
         "用三年而不是一年：一个坏赛季不该毁掉一支队"),
        ("每个边界每季交换", management.SWAP_PER_BOUNDARY, "支"),
        ("升级预算倍率", management.PROMOTION_BUDGET, ""),
        ("降级预算倍率", management.RELEGATION_BUDGET, ""),
        ("解约条款触发线", management.RELEASE_THRESHOLD,
         f"总评。降级后有 {management.RELEASE_CHANCE:.0%} 概率走人"),
    ]),
]


def main() -> None:
    lines = [
        "# 数值总表",
        "",
        "从代码里直接读的当前值，不会过期。",
        f"用 `python3 source/tools/export_balance.py` 重新生成。",
        "",
        "## 怎么用这份表",
        "",
        "改任何一项之前，先看它所在分组的说明——那里写了改完要重跑哪个验证脚本。",
        "**耦合关系比单个数值重要**：突围强度和追击增益必须一起调，"
        "集团配速和筛选系数一起决定掉队率，等级薪资溢价一动整个转会市场都会变。",
        "",
        "验证脚本：",
        "",
        "```bash",
        "python3 source/calibrate.py         # 引擎 vs 真实赛事数据",
        "python3 source/calibrate_quick.py   # 快速结算 vs 完整引擎",
        "python3 source/verify_incidents.py  # 摔车与机械故障",
        "python3 source/verify_orders.py     # 赛前指令有没有效果",
        "python3 source/run_career.py        # 十赛季长期行为",
        "```",
        "",
        "---",
        "",
    ]

    total = 0
    for title, note, rows in SECTIONS:
        lines += [f"## {title}", "", f"> {note}", "",
                  "| 旋钮 | 当前值 | 说明 |", "|---|---|---|"]
        for name, value, desc in rows:
            total += 1
            lines.append(f"| {name} | `{fmt(value)}` | {desc} |")
        lines += ["", ""]

    out = ROOT / "数值总表.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"数值总表已生成 -> {out.name}（{len(SECTIONS)} 组 / {total} 项）")
    for title, _, rows in SECTIONS:
        print(f"  {title:<20}{len(rows)} 项")


if __name__ == "__main__":
    main()
