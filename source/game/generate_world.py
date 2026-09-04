"""生成一个完整的赛季世界：赞助商、车队、车手、赛历。

    python3 source/game/generate_world.py --seed 2026 --out data/world.json

设计目标是"看一眼就知道谁是谁"：20 支队伍档次分明（三档），
每队的阵容结构反映它的战略（顶级队养总成绩核心，中游队靠冲刺手拿曝光，
洲际队全是工兵和突围专家），赛历有明确的季节节奏。

这些不是装饰。玩家第一次打开转会市场时，能不能一眼看懂"这支队缺什么"，
决定了这个游戏是策略游戏还是数字表格。
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from game.names import make_name, pick_nation  # noqa: E402
from game.world import (  # noqa: E402
    Division, Industry, RaceEvent, RaceTier, RiderProfile, Sponsor, StageSpec,
    Team, World,
)
from sim.rider import Attributes, Role  # noqa: E402
from sim.roster import ARCHETYPES  # noqa: E402

# --------------------------------------------------------------------------
# 赞助商：24 个虚构品牌，对应 24 张方形 Logo
# --------------------------------------------------------------------------

SPONSOR_SEEDS = [
    ("北纬银行", Industry.BANK), ("蓝川资本", Industry.BANK),
    ("恒通电信", Industry.TELECOM), ("速联网络", Industry.TELECOM),
    ("远瀚能源", Industry.ENERGY), ("绿岭电力", Industry.ENERGY),
    ("万家超市", Industry.RETAIL), ("优选生活", Industry.RETAIL),
    ("擎云科技", Industry.TECH), ("原点半导体", Industry.TECH),
    ("破晓数据", Industry.TECH), ("麦谷食品", Industry.FOOD),
    ("鲜野乳业", Industry.FOOD), ("初见咖啡", Industry.FOOD),
    ("凌途汽车", Industry.AUTO), ("驰远轮胎", Industry.AUTO),
    ("安泰保险", Industry.INSURANCE), ("守望人寿", Industry.INSURANCE),
    ("磐石建材", Industry.BUILDING), ("元筑工程", Industry.BUILDING),
    ("行者运动", Industry.APPAREL), ("风隼装备", Industry.APPAREL),
    ("砺行户外", Industry.APPAREL), ("云杉家居", Industry.RETAIL),
]

# --------------------------------------------------------------------------
# 队服配色：20 组，人工挑过，保证在 320px 竞速视图里彼此可分辨
# --------------------------------------------------------------------------

PALETTES = [
    ("#0B3D91", "#FFFFFF", "#F5B301"), ("#C8102E", "#1A1A1A", "#FFFFFF"),
    ("#00843D", "#FFFFFF", "#111111"), ("#FF6B00", "#1B1B3A", "#FFFFFF"),
    ("#7A1FA2", "#F2E9FF", "#FFC400"), ("#0097A7", "#003B46", "#FFFFFF"),
    ("#E4002B", "#FFD100", "#1A1A1A"), ("#2E3192", "#00AEEF", "#FFFFFF"),
    ("#1A1A1A", "#B0B0B0", "#00E676"), ("#8B5E3C", "#F0E2D0", "#2F2F2F"),
    ("#004B87", "#A8DADC", "#F1FAEE"), ("#D62828", "#F77F00", "#FCBF49"),
    ("#264653", "#2A9D8F", "#E9C46A"), ("#5F0F40", "#9A031E", "#FB8B24"),
    ("#3D348B", "#7678ED", "#F7B801"), ("#006D77", "#83C5BE", "#FFDDD2"),
    ("#22333B", "#EAE0D5", "#C6AC8F"), ("#BC4749", "#F2E8CF", "#386641"),
    ("#14213D", "#FCA311", "#E5E5E5"), ("#4A5859", "#F4E9CD", "#D62246"),
]

# --------------------------------------------------------------------------
# 车队命名与财务基准
# --------------------------------------------------------------------------

# 各等级的基础运营拨款（万元）：器材商、赛事出场费、转播分成等
BASE_GRANT = {Division.WORLD: 800, Division.PRO: 380, Division.CONTI: 120}

TEAM_COUNTRIES = ["BEL", "FRA", "ITA", "ESP", "NED", "GER", "GBR", "DEN",
                  "SUI", "AUS", "USA", "COL", "SLO", "NOR", "POR", "JPN",
                  "CHN", "BEL", "FRA", "ITA"]


def build_sponsors(rng: random.Random) -> list[Sponsor]:
    sponsors = []
    for i, (name, industry) in enumerate(SPONSOR_SEEDS):
        # 财力做成金字塔：少数几个巨头，大量中小品牌
        wealth = 5 if i < 3 else 4 if i < 8 else 3 if i < 15 else rng.randint(1, 2)
        sponsors.append(Sponsor(
            sponsor_id=f"SP{i + 1:02d}", name=name, industry=industry,
            wealth=wealth, loyalty=rng.randint(1, 5),
            art_logo=f"sponsor-logo-{i + 1:02d}",
        ))
    return sponsors


def build_teams(sponsors: list[Sponsor], rng: random.Random) -> list[Team]:
    """20 支队：6 支世巡赛、7 支职业队、7 支洲际队。

    赞助商按财力从高到低分配下去，绝不随机——一旦出现"洲际队比世巡赛队
    有钱"，玩家会立刻察觉这个世界是假的。世巡赛队双冠名（主赞助 + 副赞助），
    下面两档只挂一个名字，和现实一致。
    """
    pool = sorted(sponsors, key=lambda s: (-s.wealth, s.sponsor_id))
    cursor = 0
    teams: list[Team] = []

    for i in range(20):
        if i < 6:
            division, prestige_base, n_sp = Division.WORLD, 78, 2
        elif i < 13:
            division, prestige_base, n_sp = Division.PRO, 55, 1
        else:
            division, prestige_base, n_sp = Division.CONTI, 33, 1

        picked = []
        reused = False
        for _ in range(n_sp):
            if cursor >= len(pool):
                reused = True            # 赞助商用完了，最后几支小队共用 Logo
            picked.append(pool[min(cursor, len(pool) - 1)])
            cursor += 1

        name = "-".join(s.name for s in picked)
        # 共用赞助商的队伍必须有可区分的名字，否则赛季战报里会出现
        # 同一个队名同时升级又降级这种看不懂的画面
        if reused:
            name = f"{name}·{TEAM_COUNTRIES[i]}"
        primary, secondary, accent = PALETTES[i]

        teams.append(Team(
            team_id=f"T{i + 1:02d}", name=name,
            short_name=picked[0].name[:2],
            division=division, country=TEAM_COUNTRIES[i],
            prestige=max(10, min(99, prestige_base + rng.randint(-8, 8))),
            sponsor_ids=[s.sponsor_id for s in picked],
            art_badge=f"team-badge-{i + 1:02d}",
            jersey_pattern=(i % 10) + 1,
            color_primary=primary, color_secondary=secondary,
            color_accent=accent,
            # 预算 = 等级基础拨款 + 赞助收入。基础拨款代表器材商、
            # 赛事出场费这些不体现在队名上的收入，现实中占比不小。
            budget=BASE_GRANT[division] + sum(s.annual_budget for s in picked),
        ))
    return teams


# 每档队伍的阵容配方。顶级队养总成绩核心，中游队围绕冲刺手做曝光，
# 洲际队没有明星，只能靠突围抢镜头——这直接决定了它们在比赛里的行为。
SQUAD_RECIPES = {
    # 世巡赛队原来带两个爬坡手。**为大环赛建的队不是这么建的**——
    # 队里只有一个人有总成绩野心，其余爬得动的人是替他挡风递水的工兵。
    # 两个独立的爬坡手意味着两份野心，而他们比总成绩核心更轻、爬得更好，
    # 于是总成绩核心永远是那个输的人：按人均算，大环赛总成绩爬坡手拿
    # 5.38/百人，总成绩核心只有 1.23。
    Division.WORLD: [Role.LEADER, Role.CLIMBER, Role.DOMESTIQUE, Role.SPRINTER,
                     Role.LEADOUT, Role.ROULEUR, Role.DOMESTIQUE, Role.DOMESTIQUE],
    Division.PRO: [Role.LEADER, Role.SPRINTER, Role.LEADOUT, Role.ROULEUR,
                   Role.CLIMBER, Role.BREAKAWAY, Role.DOMESTIQUE, Role.DOMESTIQUE],
    Division.CONTI: [Role.BREAKAWAY, Role.BREAKAWAY, Role.CLIMBER, Role.ROULEUR,
                     Role.SPRINTER, Role.DOMESTIQUE, Role.DOMESTIQUE,
                     Role.DOMESTIQUE],
}

BODY_ART = {"lean": "body-lean", "standard": "body-standard",
            "strong": "body-strong"}


def build_riders(teams: list[Team], rng: random.Random) -> list[RiderProfile]:
    riders: list[RiderProfile] = []
    counter = 0

    for team in teams:
        tier = {Division.WORLD: 1.0, Division.PRO: 0.68,
                Division.CONTI: 0.40}[team.division]
        tier += (team.prestige - 60) / 400.0

        for slot, role in enumerate(SQUAD_RECIPES[team.division]):
            counter += 1
            spec = ARCHETYPES[role]
            base = 34 + 34 * max(0.0, min(1.0, tier))
            mass = rng.uniform(*spec["mass"])
            offs = spec["offs"]

            # 年龄影响当前能力与剩余潜力：22 岁的人现在弱但还能涨，
            # 33 岁的人接近满值但很快开始掉。这是转会市场的核心张力。
            age = rng.choices([22, 24, 26, 28, 30, 32, 34],
                              weights=[10, 16, 20, 18, 14, 12, 10])[0]
            age_curve = 1.0 - abs(age - 28) * 0.014
            raw_potential = base + rng.gauss(10, 6) + max(0, 28 - age) * 1.2

            def roll(key: str) -> int:
                # 护栏：随机波动不能大到把角色标签变成谎言。一个"平路手"
                # 如果爬坡属性掷出高分，他实际上就是个总成绩核心，会去赢
                # 大环赛——玩家看到阵容表上写着"平路手"却在爬坡赛称王，
                # 会认为这个游戏的标签是随便贴的。所以定义性属性的下偏移
                # 只允许小幅回补，上限锁死。
                v = base * age_curve + offs.get(key, 0) + rng.gauss(0, 5)
                cap = offs.get(key, 0) + (4 if offs.get(key, 0) < -8 else 99)
                return int(round(min(v, base * age_curve + cap)))

            attrs = Attributes(
                flat=roll("flat"), climbing=roll("climbing"), sprint=roll("sprint"),
                time_trial=roll("time_trial"), descending=roll("descending"),
                endurance=roll("endurance"), recovery=roll("recovery"),
                positioning=roll("positioning"), resilience=roll("resilience"),
            ).clamped()

            body = ("lean" if mass < 64 else "strong" if mass > 74 else "standard")
            nation = pick_nation(rng)
            profile = RiderProfile(
                rider_id=f"R{counter:03d}",
                name=make_name(nation, rng),
                nation=nation, age=age,
                body_mass_kg=round(mass, 1),
                height_cm=int(round(168 + (mass - 65) * 0.9 + rng.gauss(0, 3))),
                team_id=team.team_id, role=role, attributes=attrs,
                potential=40,          # 下面按总评修正为真正的天花板
                art_portrait=f"portrait-{(counter - 1) % 32 + 1:02d}",
                art_body=BODY_ART[body],
                contract_years=rng.randint(1, 3),
                form=round(rng.gauss(1.0, 0.035), 3),
                morale=round(rng.uniform(0.92, 1.08), 3),
                career_wins=max(0, int(rng.gauss((age - 22) * 0.8, 2))),
            )
            # 潜力是成长天花板，按定义不能低于当前水平。年轻人天花板高、
            # 头顶空间大；30 岁以后基本就是"现在什么样，以后还是什么样"。
            profile.potential = int(min(99, max(raw_potential,
                                                profile.overall + (age < 27) * 2)))
            profile.salary = _salary(profile, team.division)
            riders.append(profile)
            team.rider_ids.append(profile.rider_id)

    return riders


# 角色溢价：市场为"能赢比赛的人"付钱。同样 80 分的总成绩核心和领骑员，
# 前者是队伍的全部投资理由，后者随时可以换一个——薪资必须体现这个差别，
# 否则转会市场会退化成"按总评排序买最贵的"。
ROLE_VALUE = {
    Role.LEADER: 1.55, Role.SPRINTER: 1.30, Role.CLIMBER: 1.15,
    Role.ROULEUR: 0.95, Role.BREAKAWAY: 0.85, Role.LEADOUT: 0.80,
    Role.DOMESTIQUE: 0.70,
}

# 同一名车手在世巡赛队能拿到的钱是洲际队的六倍以上。这不是数值失衡，
# 现实就是如此——它也是整个经营层的驱动力：小队永远留不住自己培养的人。
DIVISION_PREMIUM = {Division.WORLD: 3.2, Division.PRO: 1.0,
                    Division.CONTI: 0.5}


def _salary(profile: RiderProfile, division: Division) -> int:
    """年薪（万元）。

    对总评取高次幂：这个市场极度头重脚轻，90 分的人不是 70 分的 1.3 倍贵，
    而是十几倍贵。现实如此，也让"砸锅卖铁签一个巨星"成为一个真实的两难。
    """
    age_factor = 1.0 - abs(profile.age - 27) * 0.022
    youth_bonus = 1.0 + max(0, profile.potential - profile.overall) * 0.006
    base = (profile.overall ** 2.6) / 750
    return int(max(8, base * ROLE_VALUE[profile.role]
                   * DIVISION_PREMIUM[division] * age_factor * youth_bonus))


# --------------------------------------------------------------------------
# 赛历
# --------------------------------------------------------------------------

# (名称, 等级, 国家, 起始日, 声望, 赛段配方, 地形)
# 赛段配方用字母缩写：F 平路 / H 丘陵 / M 山地 / S 山顶终点 / I 计时 / C 石板
CALENDAR_SEEDS = [
    ("海岸开季赛",   RaceTier.PRO_SERIES, "ESP", 20,  42, "FF",        "coast"),
    ("春季丘陵赛",   RaceTier.WORLD_TOUR, "ITA", 33,  58, "H",         "italian_hills"),
    ("石板路经典赛", RaceTier.MONUMENT,   "BEL", 48,  92, "C",         "cobbles"),
    ("北方之战",     RaceTier.MONUMENT,   "BEL", 55,  94, "C",         "cobbles"),
    ("低地绕圈赛",   RaceTier.PRO_SERIES, "NED", 62,  40, "F",         "dutch"),
    ("阿登丘陵赛",   RaceTier.MONUMENT,   "BEL", 70,  88, "S",         "italian_hills"),
    ("春季山地赛",   RaceTier.WORLD_TOUR, "ITA", 78,  62, "MS",        "alps"),
    ("五月大环赛",   RaceTier.GRAND_TOUR, "ITA", 92, 100, "FHIFMSFMSFHFSFHIFMSF", "italian_hills"),
    ("城市计时赛",   RaceTier.PRO_SERIES, "GER", 122, 38, "I",         "city"),
    ("阿尔卑斯八日", RaceTier.WORLD_TOUR, "SUI", 130, 66, "FHMSIFMS",  "alps"),
    ("环荷兰",       RaceTier.PRO_SERIES, "NED", 142, 36, "FFF",       "dutch"),
    ("比利牛斯五日", RaceTier.WORLD_TOUR, "FRA", 150, 64, "FHMSI",     "pyrenees"),
    ("盛夏大环赛",   RaceTier.GRAND_TOUR, "FRA", 165, 100, "FFHMSIFMSFHFSFMSFHIFF", "pyrenees"),
    ("沙漠挑战赛",   RaceTier.PRO_SERIES, "ESP", 196, 34, "FF",        "desert"),
    ("海滨冲刺赛",   RaceTier.PRO_SERIES, "POR", 202, 32, "F",         "coast"),
    ("环英国",       RaceTier.WORLD_TOUR, "GBR", 208, 56, "FHFHI",     "coast"),
    ("秋季大环赛",   RaceTier.GRAND_TOUR, "ESP", 218, 98, "FHMSFIFMSFHFSFMSFHFI", "alps"),
    ("北欧丘陵赛",   RaceTier.PRO_SERIES, "NOR", 250, 38, "H",         "coast"),
    ("城市夜赛",     RaceTier.NATIONAL,   "JPN", 256, 26, "F",         "city"),
    ("环中国",       RaceTier.PRO_SERIES, "CHN", 262, 34, "FFH",       "city"),
    ("落叶经典赛",   RaceTier.MONUMENT,   "ITA", 272, 90, "S",         "italian_hills"),
    ("山地告别赛",   RaceTier.WORLD_TOUR, "SUI", 278, 60, "MS",        "alps"),
    ("收官计时赛",   RaceTier.PRO_SERIES, "FRA", 284, 40, "I",         "city"),
    ("荒漠绕圈赛",   RaceTier.NATIONAL,   "AUS", 290, 24, "F",         "desert"),
    ("赛季总决赛",   RaceTier.WORLD_TOUR, "ITA", 296, 70, "HS",        "italian_hills"),
]

STAGE_LETTER = {
    "F": ("flat", (168, 205)),
    "H": ("hilly", (155, 195)),
    "M": ("mountain", (150, 185)),
    "S": ("summit_finish", (140, 178)),
    "I": ("itt", (14, 45)),
    "C": ("cobbled", (185, 260)),
    # 团体计时赛。真实的大环赛常用它当开幕赛段——短、快、把全队摆上台面，
    # 而且第一天就能拉开一两分钟，为整个三周定调。
    "T": ("ttt", (22, 38)),
}

# 只有六大赛有专属徽章，其余用底色 + 引擎排版赛事名（美术清单的决定）
BADGE_RACES = ["石板路经典赛", "北方之战", "阿登丘陵赛", "五月大环赛",
               "盛夏大环赛", "秋季大环赛"]


def calendar_rows() -> list[tuple]:
    """赛历数据源。有 data/calendar.json 就以文件为准，没有才用内置默认。

    和赛道一个规矩：内置的那张表从「唯一来源」降级成「给你一份初稿」。
    """
    from game.calendar_io import load

    rows = load()
    if rows is None:
        return CALENDAR_SEEDS
    return [(r["name"], RaceTier(r["tier"]), r["country"], r["start_day"],
             r["prestige"], r["stages"], r["terrain"]) for r in rows]


def build_calendar(rng: random.Random) -> list[RaceEvent]:
    events: list[RaceEvent] = []
    for i, (name, tier, country, day, prestige, recipe, terrain) in \
            enumerate(calendar_rows()):
        stages = []
        for j, letter in enumerate(recipe):
            stype, (lo, hi) = STAGE_LETTER[letter]
            # 大环赛的赛段地形要有变化，不能 20 天都是同一块背景板
            st_terrain = terrain
            if len(recipe) > 5:
                st_terrain = {"mountain": terrain, "summit_finish": terrain,
                              "itt": "city", "flat": "dutch",
                              "hilly": "italian_hills",
                              "cobbled": "cobbles"}.get(stype, terrain)
            stages.append(StageSpec(
                index=j + 1,
                name=f"第 {j + 1} 赛段" if len(recipe) > 1 else name,
                stage_type=stype,
                length_km=round(rng.uniform(lo, hi), 1),
                terrain=st_terrain,
                seed=rng.randrange(1, 10 ** 6),
            ))
        badge_idx = BADGE_RACES.index(name) + 1 if name in BADGE_RACES else 0
        events.append(RaceEvent(
            race_id=f"E{i + 1:02d}", name=name, tier=tier, country=country,
            start_day=day, prestige=prestige, stages=stages,
            art_badge=f"race-badge-{badge_idx:02d}" if badge_idx else "",
            art_landmark=stages[0].art_landmark,
        ))
    return events


def assign_season_targets(world: World, rng: random.Random) -> None:
    """给每支队的核心车手排本季主攻的赛事。

    现实中没有人会同时全力打三个大环赛——身体撑不住，赞助商也不需要。
    每支队各自挑一个主攻的大环赛，再点几场适合自己车手的经典赛。
    这条规则单独存在的意义是：它让"赛季规划"成为玩法，也让冠军奖杯
    分散到不同人手里，而不是被最强的那一个人全部拿走。
    """
    grand_tours = [e for e in world.calendar if e.tier is RaceTier.GRAND_TOUR]
    monuments = [e for e in world.calendar if e.tier is RaceTier.MONUMENT]
    hilly_monuments = [e for e in monuments
                       if e.stages[0].stage_type in ("hilly", "summit_finish")]
    flat_monuments = [e for e in monuments if e.stages[0].stage_type == "cobbled"]

    for i, team in enumerate(world.teams):
        roster = world.roster(team.team_id)
        # 每支队的主攻大环赛轮着来，保证三个大环赛都有强队认真打
        home_gt = grand_tours[i % len(grand_tours)] if grand_tours else None

        # 短程分站赛（一周以内的多日赛）：爬坡手的主场。
        short_tours = [e for e in world.calendar
                       if e.is_stage_race and e.days <= 9]

        for r in roster:
            targets: list[str] = []
            if r.role is Role.LEADER:
                # 总成绩核心一年就打一个大环赛。他的整个赛季是为那三周
                # 服务的，这也是他和爬坡手最本质的区别。
                if home_gt:
                    targets.append(home_gt.race_id)
                targets += [e.race_id for e in hilly_monuments
                            if rng.random() < 0.30]
            elif r.role is Role.CLIMBER:
                # **爬坡手不打大环赛总成绩，他打山地赛段和短程分站赛。**
                # 原来这两个角色的目标赛事完全一样，于是他们在同一批比赛里
                # 抢同一个位置——而爬坡手更轻、爬得更好，总成绩核心永远是
                # 那个输的人。角色要区分开，光调属性不够，得让他们去不同的
                # 比赛：现实里的纯爬坡手就是靠赛段胜利和小型环赛立身的。
                targets += [e.race_id for e in short_tours
                            if rng.random() < 0.55]
                targets += [e.race_id for e in hilly_monuments
                            if rng.random() < 0.55]
                if home_gt and rng.random() < 0.5:
                    targets.append(home_gt.race_id)
            elif r.role is Role.SPRINTER:
                # 冲刺手一年可以打两个大环赛，赛段机会才是他们的目标
                picks = rng.sample(grand_tours, min(2, len(grand_tours)))
                targets += [e.race_id for e in picks]
                targets += [e.race_id for e in flat_monuments]
            elif r.role is Role.ROULEUR:
                targets += [e.race_id for e in flat_monuments]
                if rng.random() < 0.4 and home_gt:
                    targets.append(home_gt.race_id)
            # 工兵、领骑员、突围手不设目标：他们全年待命，去哪儿都行
            r.target_races = targets


def generate(seed: int = 2026, season: int = 2026) -> World:
    rng = random.Random(seed)
    sponsors = build_sponsors(rng)
    teams = build_teams(sponsors, rng)
    riders = build_riders(teams, rng)
    calendar = build_calendar(rng)
    world = World(season, sponsors, teams, riders, calendar)
    assign_season_targets(world, rng)
    return world


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--out", default="data/world.json")
    args = ap.parse_args()

    world = generate(args.seed, args.season)
    root = Path(__file__).resolve().parents[2]
    path = world.save(root / args.out)

    print(f"赛季 {world.season} 世界已生成 -> {path}")
    print(f"  赞助商 {len(world.sponsors)}  车队 {len(world.teams)}  "
          f"车手 {len(world.riders)}  赛事 {len(world.calendar)}"
          f"（共 {sum(e.days for e in world.calendar)} 个比赛日）")

    print("\n各档次车队：")
    for div in ("世巡赛", "职业队", "洲际队"):
        group = [t for t in world.teams if t.division.value == div]
        avg = sum(max(r.overall for r in world.roster(t.team_id))
                  for t in group) / len(group)
        print(f"  {div:<5} {len(group)} 支，预算 "
              f"{min(t.budget for t in group)}–{max(t.budget for t in group)} 万，"
              f"头号车手平均 {avg:.0f} 分")

    print("\n各队财务（薪资占预算比例）：")
    for div in (Division.WORLD, Division.PRO, Division.CONTI):
        group = [t for t in world.teams if t.division is div]
        ratios = [sum(r.salary for r in world.roster(t.team_id)) / t.budget
                  for t in group]
        print(f"  {div.value:<5} {min(ratios):.0%} – {max(ratios):.0%}"
              f"（超过 100% 即为超支，需要玩家自己解决）")

    print("\n身价最高的十名车手：")
    for r in sorted(world.riders, key=lambda r: -r.salary)[:10]:
        tm = world.team(r.team_id)
        print(f"  {r.name:<20}{r.nation}  {r.age}岁  {r.role.value:<10}"
              f"总评 {r.overall:>3}  潜力 {r.potential:>3}  "
              f"年薪 {r.salary:>5} 万  {tm.short_name}（{tm.division.value}）")

    print("\n赛季节奏：")
    for e in world.calendar:
        if e.tier.value in ("大环赛", "纪念碑"):
            print(f"  第 {e.start_day:>3} 天  {e.name:<12}{e.tier.value:<5}"
                  f"{e.days:>2} 天  声望 {e.prestige}")


if __name__ == "__main__":
    main()
