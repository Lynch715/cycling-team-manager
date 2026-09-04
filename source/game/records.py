"""成绩数据库：把跑过的每一场比赛留下来。

在这之前，一个赛季跑完除了积分和几条动态之外什么都不剩。谁在哪条赛道上
创造过最好成绩、某个车手职业生涯赢过几场、某支队的历史最佳排名——全都
没有记录。

这一层做两件事，而且第二件比第一件重要：

**一、给玩家的历史。** 经理游戏的长期黏性很大一部分来自「翻看历史」——
十个赛季之后回头看自己第一年签下的那个 22 岁工兵，现在是名人堂第三位。
没有记录，长线游戏就没有重量。

**二、给设计者的诊断。** 这份数据是最好的平衡工具：跑二十个赛季，
如果冠军永远是同一类车手，说明角色平衡有问题；如果赛道纪录永远由
同一条赛道保持，说明赛道设计有同质化。这些结论在单个赛季里完全看不出来。

存储用 SQLite 而不是 JSON：二十个赛季有两千多条比赛结果，JSON 每次都要
全量读写，而且没法查询。SQLite 是标准库自带的，不引入任何依赖。
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "records.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY,
    season      INTEGER NOT NULL,
    race_id     TEXT    NOT NULL,
    race_name   TEXT    NOT NULL,
    tier        TEXT    NOT NULL,
    stage_index INTEGER NOT NULL DEFAULT 0,   -- 0 表示总成绩
    stage_type  TEXT,
    course_id   TEXT,
    place       INTEGER NOT NULL,
    rider_id    TEXT    NOT NULL,
    rider_name  TEXT    NOT NULL,
    nation      TEXT,
    age         INTEGER,
    role        TEXT,
    team_id     TEXT,
    team_name   TEXT,
    time_s      REAL,
    gap_s       REAL,
    points      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_results_season  ON results(season);
CREATE INDEX IF NOT EXISTS ix_results_rider   ON results(rider_id);
CREATE INDEX IF NOT EXISTS ix_results_race    ON results(race_id, stage_index);
CREATE INDEX IF NOT EXISTS ix_results_course  ON results(course_id);

CREATE TABLE IF NOT EXISTS seasons (
    season      INTEGER NOT NULL,
    team_id     TEXT    NOT NULL,
    team_name   TEXT,
    division    TEXT,
    rank        INTEGER,
    points      INTEGER,
    budget      INTEGER,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS rider_seasons (
    season      INTEGER NOT NULL,
    rider_id    TEXT    NOT NULL,
    rider_name  TEXT,
    team_id     TEXT,
    age         INTEGER,
    role        TEXT,
    overall     INTEGER,
    points      INTEGER,
    wins        INTEGER DEFAULT 0,
    race_days   INTEGER DEFAULT 0,
    PRIMARY KEY (season, rider_id)
);
"""


_MIRRORS: dict[int, tuple[Path, Path]] = {}   # id(conn) -> (临时文件, 目标文件)


def _writable(p: Path) -> bool:
    """SQLite 需要真正的文件锁。网盘、同步盘、容器挂载点经常不支持，
    表现为一句莫名其妙的 'disk I/O error'。先探一下，别等写到一半才炸。"""
    probe = p.parent / f".{p.name}.probe"
    try:
        c = sqlite3.connect(probe)
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()
        return True
    except sqlite3.Error:
        return False
    finally:
        for f in (probe, Path(str(probe) + "-journal")):
            try:
                f.unlink()
            except OSError:
                pass


def connect(path: Path | None = None) -> sqlite3.Connection:
    """打开成绩库。目标目录不支持文件锁时，自动改用本地临时副本，
    由 flush() 写回——对调用方完全透明。"""
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)

    real = p
    if not _writable(p):
        tmp = Path(tempfile.gettempdir()) / f"records-{abs(hash(str(p)))}.db"
        if p.exists():
            shutil.copy2(p, tmp)
        real = tmp

    conn = sqlite3.connect(real)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if real != p:
        _MIRRORS[id(conn)] = (real, p)
    return conn


def flush(conn: sqlite3.Connection) -> None:
    """提交，并在使用临时副本时写回目标位置。"""
    conn.commit()
    pair = _MIRRORS.get(id(conn))
    if pair:
        tmp, dest = pair
        shutil.copy2(tmp, dest)


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------

def record_stage(conn, season: int, race_id: str, race_name: str, tier: str,
                 stage_index: int, course_id: str | None,
                 order: list[str], times: dict[str, float],
                 world, top: int = 12, stage_type: str | None = None) -> None:
    """记一个赛段的成绩。只存前 top 名——第 87 名是谁没人会去查，
    而全存会让二十赛季的库涨到几十万行。

    12 是权衡的结果：前十是玩家会看的，多留两个位置给「差一点上榜」
    的展示。存 30 名的话二十五赛季要 17 MB，存 12 名只要 7 MB。"""
    if not order:
        return
    win_t = times.get(order[0], 0.0)
    rows = []
    for place, rid in enumerate(order[:top], start=1):
        try:
            r = world.rider(rid)
        except StopIteration:
            continue
        t = world.team(r.team_id) if r.team_id else None
        rows.append((season, race_id, race_name, tier, stage_index,
                     stage_type, course_id,
                     place, rid, r.name, r.nation, r.age, r.role.value,
                     r.team_id, t.name if t else "",
                     times.get(rid), times.get(rid, 0.0) - win_t, 0))
    conn.executemany(
        "INSERT INTO results (season,race_id,race_name,tier,stage_index,"
        "stage_type,course_id,place,rider_id,rider_name,nation,age,role,"
        "team_id,team_name,time_s,gap_s,points) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)


def record_season(conn, season: int, world, team_points: dict[str, int],
                  rider_points: dict[str, int],
                  race_days: dict[str, int] | None = None,
                  wins: dict[str, int] | None = None) -> None:
    ranking = sorted(team_points.items(), key=lambda x: -x[1])
    conn.executemany(
        "INSERT OR REPLACE INTO seasons VALUES (?,?,?,?,?,?,?)",
        [(season, tid, world.team(tid).name, world.team(tid).division.value,
          i + 1, pts, world.team(tid).budget)
         for i, (tid, pts) in enumerate(ranking)])

    rows = []
    for r in world.riders:
        rows.append((season, r.rider_id, r.name, r.team_id, r.age,
                     r.role.value, r.overall, rider_points.get(r.rider_id, 0),
                     (wins or {}).get(r.rider_id, 0),
                     (race_days or {}).get(r.rider_id, 0)))
    conn.executemany(
        "INSERT OR REPLACE INTO rider_seasons VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    flush(conn)


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------

@dataclass
class Row:
    """查询结果的通用容器，方便前端直接序列化。"""

    label: str
    value: str
    extra: str = ""


def palmares(conn, rider_id: str) -> list[dict]:
    """一名车手的战绩表。"""
    cur = conn.execute(
        "SELECT season, race_name, tier, stage_index, place FROM results "
        "WHERE rider_id=? AND place<=10 ORDER BY season, race_name", (rider_id,))
    return [dict(r) for r in cur.fetchall()]


def career_wins(conn, limit: int = 15) -> list[dict]:
    """历史夺冠榜。"""
    cur = conn.execute(
        "SELECT rider_id, rider_name, COUNT(*) AS wins, "
        "       SUM(CASE WHEN tier IN ('大环赛','纪念碑') THEN 1 ELSE 0 END) AS big "
        "FROM results WHERE place=1 GROUP BY rider_id "
        "ORDER BY big DESC, wins DESC LIMIT ?", (limit,))
    return [dict(r) for r in cur.fetchall()]


def course_records(conn, limit: int = 12) -> list[dict]:
    """赛道纪录：每条赛道跑过的最快成绩。

    这是纯粹为玩家做的东西，但它同时也是一个诊断信号——如果某条赛道的
    纪录从第一个赛季起就没被打破过，说明那一年的世界水平明显偏高。
    """
    cur = conn.execute(
        "SELECT course_id, race_name, MIN(time_s) AS best, "
        "       rider_name, season FROM results "
        "WHERE place=1 AND time_s IS NOT NULL AND course_id IS NOT NULL "
        "GROUP BY course_id ORDER BY race_name LIMIT ?", (limit,))
    return [dict(r) for r in cur.fetchall()]


def role_balance(conn) -> list[dict]:
    """**最重要的一个诊断查询**：历史上各类车手各赢了多少场。

    如果冠军永远是同一类车手，说明角色平衡崩了——而这个结论在单个赛季里
    完全看不出来，只有把几十个赛季堆在一起才显形。

    关键是 `per100`（每一百个车手赛季赢几场），不是 `wins`。原始胜场数
    必然是工兵最多——他们占了整个车队一半人。**只有除以人数，才知道
    「当一名爬坡手」和「当一名工兵」哪个更有价值。** 看原始数字会得出
    完全相反的结论。
    """
    pop = {r["role"]: r["n"] for r in conn.execute(
        "SELECT role, COUNT(*) AS n FROM rider_seasons GROUP BY role")}
    cur = conn.execute(
        "SELECT role, COUNT(*) AS wins, "
        "       SUM(CASE WHEN stage_index=0 THEN 1 ELSE 0 END) AS overalls, "
        "       SUM(CASE WHEN tier='大环赛' THEN 1 ELSE 0 END) AS gt, "
        "       SUM(CASE WHEN tier='纪念碑' THEN 1 ELSE 0 END) AS mon "
        "FROM results WHERE place=1 GROUP BY role ORDER BY wins DESC")
    out = []
    for r in cur.fetchall():
        d = dict(r)
        n = pop.get(d["role"], 0)
        d["pop"] = n
        d["per100"] = round(d["wins"] * 100.0 / n, 1) if n else None
        out.append(d)
    out.sort(key=lambda d: -(d["per100"] or 0))
    return out


def wins_by_stage_type(conn) -> list[dict]:
    """按赛段类型看谁在赢。

    `role_balance` 只能告诉你「冲刺手赢得多」，但那可能只是因为平路赛段多。
    **真正的问题是：冲刺手在赢山地赛段吗？** 那才说明地形没有起到筛选作用。
    """
    cur = conn.execute(
        "SELECT stage_type, role, COUNT(*) AS wins FROM results "
        "WHERE place=1 AND stage_type IS NOT NULL GROUP BY stage_type, role")
    grid: dict[str, dict[str, int]] = {}
    for r in cur.fetchall():
        grid.setdefault(r["stage_type"], {})[r["role"]] = r["wins"]
    out = []
    for st, roles in sorted(grid.items()):
        total = sum(roles.values())
        out.append({"stage_type": st, "total": total,
                    "shares": {k: round(v * 100.0 / total, 1)
                               for k, v in sorted(roles.items(),
                                                  key=lambda x: -x[1])}})
    return out


def gc_by_role(conn, tier: str = "大环赛") -> list[dict]:
    """大环赛总成绩冠军的角色分布，**按人均**。

    绝对数在这里是骗人的：世界上每支队只有一名总成绩核心，却有两名爬坡手，
    所以爬坡手的绝对夺冠数天然是两倍。要回答「总成绩核心这个角色到底
    称不称职」，只能看每一百个「车手赛季」拿到几个总成绩冠军。
    """
    pop = {r["role"]: r["n"] for r in conn.execute(
        "SELECT role, COUNT(*) AS n FROM rider_seasons GROUP BY role")}
    cur = conn.execute(
        "SELECT role, COUNT(*) AS wins FROM results "
        "WHERE place=1 AND stage_type='gc' AND tier=? GROUP BY role", (tier,))
    out = []
    for r in cur.fetchall():
        n = pop.get(r["role"], 0)
        out.append({"role": r["role"], "wins": r["wins"], "pop": n,
                    "per100": round(r["wins"] * 100.0 / n, 2) if n else None})
    out.sort(key=lambda d: -(d["per100"] or 0))
    return out


def nation_medals(conn, limit: int = 10) -> list[dict]:
    cur = conn.execute(
        "SELECT nation, COUNT(*) AS wins FROM results "
        "WHERE place=1 AND nation IS NOT NULL "
        "GROUP BY nation ORDER BY wins DESC LIMIT ?", (limit,))
    return [dict(r) for r in cur.fetchall()]


def age_of_winners(conn) -> list[dict]:
    """夺冠年龄分布。真实公路车的夺冠年龄集中在 26-31。

    如果这个分布明显偏离，说明成长曲线或老化曲线有问题。
    """
    cur = conn.execute(
        "SELECT CASE WHEN age<=23 THEN '≤23' WHEN age<=26 THEN '24-26' "
        "            WHEN age<=29 THEN '27-29' WHEN age<=32 THEN '30-32' "
        "            ELSE '33+' END AS band, COUNT(*) AS wins "
        "FROM results WHERE place=1 AND age IS NOT NULL "
        "GROUP BY band ORDER BY band")
    return [dict(r) for r in cur.fetchall()]


def team_history(conn, team_id: str) -> list[dict]:
    cur = conn.execute(
        "SELECT season, division, rank, points, budget FROM seasons "
        "WHERE team_id=? ORDER BY season", (team_id,))
    return [dict(r) for r in cur.fetchall()]


def dynasty_check(conn) -> list[dict]:
    """哪支队统治了多少个赛季。连霸太多说明反馈回路失控。"""
    cur = conn.execute(
        "SELECT team_id, team_name, COUNT(*) AS titles FROM seasons "
        "WHERE rank=1 GROUP BY team_id ORDER BY titles DESC")
    return [dict(r) for r in cur.fetchall()]


def world_level(conn) -> list[dict]:
    """每个赛季前 20 名车手的平均总评。逐年下滑说明世界在变弱。"""
    cur = conn.execute(
        "SELECT season, ROUND(AVG(overall), 1) AS level FROM ("
        "  SELECT season, overall, ROW_NUMBER() OVER "
        "    (PARTITION BY season ORDER BY overall DESC) AS rn "
        "  FROM rider_seasons) WHERE rn<=20 GROUP BY season ORDER BY season")
    return [dict(r) for r in cur.fetchall()]


def summary(conn) -> dict:
    with closing(conn.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT season) s, "
            "COUNT(DISTINCT rider_id) r FROM results")) as cur:
        row = cur.fetchone()
    return {"rows": row["n"], "seasons": row["s"], "riders": row["r"]}
