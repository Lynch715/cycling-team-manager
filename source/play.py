"""可玩版本：一条命令启动，浏览器里从接手车队玩到赛季结束。

    python3 source/play.py

**为什么是本地服务端，不是纯静态 HTML。** 前面那些原型都是把 Python
算好的结果冻进 HTML 里——能看，不能玩。要真的能玩，游戏逻辑必须能在
玩家点下按钮之后运行。三条路：

  把引擎移植成 JS —— 两套数值实现，早晚不一致，这是最贵的错误
  预演所有分支    —— 组合爆炸，而且玩家的选择就不再是选择
  **Python 当服务端** —— 引擎只有一份，前端只负责显示和收集输入

第三条还顺带验证了整个项目一直宣称的架构：比赛引擎是一个纯数值服务，
换成 Unity 或 Godot 前端时，换掉的只有这一层 HTTP 之上的东西。

服务端只用标准库的 http.server，不装任何东西。它只监听 127.0.0.1，
不对外开放——这是一个单机游戏的壳，不是一个 web 服务。
"""

from __future__ import annotations

import json
import mimetypes
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))

from game import assets                                    # noqa: E402
from game.career import Career, DecisionKind, new_career    # noqa: E402
from game.orders import EFFECTS, Order, PLAYBOOKS          # noqa: E402
from game.course_report import analyse                       # noqa: E402
from game.courses import build_course                        # noqa: E402
from game.world import TERRAIN_ART                            # noqa: E402
from game.training import CAMPS, PROGRAMS                     # noqa: E402

ART = ROOT / "assets" / "art"
APP_HTML = ROOT / "source" / "tools" / "play_app.html"
SAVE = ROOT / "data" / "playthrough.json"

ROLE_CN = {
    "sprinter": "冲刺手", "climber": "爬坡手", "rouleur": "全能型",
    "leader": "总成绩核心", "domestique": "工兵", "leadout": "冲刺列车",
    "breakaway": "突围手",
}
TYPE_CN = {
    "flat": "平路", "hilly": "丘陵", "mountain": "山地",
    "summit_finish": "山顶终点", "cobbled": "石板路", "itt": "个人计时",
    "ttt": "团体计时",
}
ATTR_CN = {
    "flat": "平路", "climbing": "爬坡", "sprint": "冲刺",
    "time_trial": "计时", "descending": "下坡", "endurance": "耐力",
    "recovery": "恢复", "positioning": "抢位", "resilience": "韧性",
}


# --------------------------------------------------------------------------
# 状态序列化：前端需要什么，这里就给什么
# --------------------------------------------------------------------------

ORDER_NOTE = {
    "护航队长": "全程为被保护的队友挡风。自己几乎不可能有成绩，但队友省下的力气是实打实的。",
    "自由发挥": "按角色本能行动。不给指令的人就是这样。",
    "抢突围": "开场就往前冲，赌一次远距离逃脱。多数时候被抓回，偶尔赢下一整天。",
    "冲刺争胜": "全程躲风保存体力，终点前争名次。他需要有人替他挡风。",
    "带冲刺列车": "最后三公里把队长拉到前排，然后退出。自己不争。",
    "山地进攻": "在陡坡上找机会攻击。要么甩开所有人，要么自己先崩。",
    "保存体力": "少花力气，为后面的赛段留着。今天的成绩会差一点。",
    "全程干活": "领骑、控节奏、拖回掉队的人。最累的活。",
}


def orders_json() -> list[dict]:
    """把八条指令连同它们在数值上的实际含义一起送给前端。

    只给名字是不够的——玩家得知道「护航队长」到底扣了什么、补了什么。
    这些数字本来就是公开的设计意图，藏起来只会让人乱点。
    """
    out = []
    for o in Order:
        d = EFFECTS[o]
        out.append({"name": o.value, "note": ORDER_NOTE.get(o.value, ""),
                    "pull": d.pull_bias, "attack": d.attack_bias,
                    "spend": d.spend_bias, "sprint": d.sprint_bias,
                    "conserve": d.conserve})
    return out


def art_url(logical: str) -> str | None:
    rel = assets.resolve(logical)
    return f"/art/{rel}" if rel else None


def rider_json(r, mine: bool = False) -> dict:
    a = r.attributes
    return {
        "id": r.rider_id, "name": r.name, "nation": r.nation, "age": r.age,
        "role": r.role.value, "role_cn": ROLE_CN.get(r.role.value, r.role.value),
        "overall": r.overall, "potential": r.potential,
        "salary": r.salary, "contract": r.contract_years,
        "form": round(r.form, 3), "morale": round(r.morale, 3),
        "fatigue": round(r.fatigue, 3), "wins": r.career_wins,
        "mass": r.body_mass_kg, "height": r.height_cm,
        "portrait": art_url(r.art_portrait),
        "attrs": {k: getattr(a, k) for k in ATTR_CN},
        "training": r.training or {},
        "mine": mine,
    }


def team_json(w, t) -> dict:
    return {
        "id": t.team_id, "name": t.name, "short": t.short_name,
        "division": t.division.value, "country": t.country,
        "prestige": t.prestige, "budget": t.budget,
        "color": t.color_primary, "color2": t.color_secondary,
        "accent": t.color_accent, "badge": art_url(t.art_badge),
        "sponsors": [{"name": s.name, "logo": art_url(s.art_logo)}
                     for s in (w.sponsor(i) for i in t.sponsor_ids)],
        "points_history": t.points_history,
    }


# 赛道是程序化生成的，拼一条要花点时间。同一条赛道在一个赛季里会被
# 反复问到（赛历页、赛前页、结果页），所以缓存——但只缓存这一层，
# 生成逻辑本身不动。
_COURSE_CACHE: dict[str, dict] = {}


def _terrain_art(terrain: str) -> str | None:
    """地形 → 地貌立绘。TERRAIN_ART 里第二个元素正好就是 landmark 的文件名。"""
    key = TERRAIN_ART.get(terrain)
    if not key:
        return None
    return art_url(key[1])


def _parallax(terrain: str, layer: str) -> str | None:
    folder = (TERRAIN_ART.get(terrain) or ("alpine",))[0]
    rel = f"05_race_parallax/{folder}/final/{layer}.png"
    return f"/art/{rel}" if (ART / rel).exists() else None


def _profile(course, points: int = 96) -> list[int]:
    """把赛道压成一条给前端画的高度折线。

    直接用 Course 自己算好的 _cum / _alt 采样，而不是另写一遍累加——
    画出来的剖面必须和引擎跑的是同一条路，重算一遍就是给自己留分歧的口子。

    只取 96 个点：再多一格也看不出来，而 21 个赛段各传两千个点会让
    每次刷新都传几百 KB。
    """
    cum, alt = course._cum, course._alt
    total = cum[-1]
    out, k = [], 0
    for i in range(points):
        x = total * i / (points - 1)
        while k + 2 < len(cum) and cum[k + 1] < x:
            k += 1
        span = max(1.0, cum[k + 1] - cum[k])
        f = min(1.0, max(0.0, (x - cum[k]) / span))
        out.append(alt[k] + (alt[k + 1] - alt[k]) * f)
    lo = min(out)
    return [round(v - lo) for v in out]


def stage_detail(e, i, spec) -> dict:
    key = f"{e.race_id}-{i}"
    if key not in _COURSE_CACHE:
        course = build_course(spec, e.name)
        rep = analyse(course, spec.stage_type)
        _COURSE_CACHE[key] = {
            "index": i + 1, "name": spec.name,
            "type": spec.stage_type,
            "type_cn": TYPE_CN.get(spec.stage_type, spec.stage_type),
            "km": round(course.length_m / 1000, 1),
            "ascent": round(course.total_ascent_m),
            "terrain": spec.terrain,
            # 地貌图按赛段的地形来取，不是按赛事序号随便挑一张——
            # 山地赛段配一张海岸照片，玩家一眼就知道这游戏在敷衍他
            "art": _terrain_art(spec.terrain),
            "sky": _parallax(spec.terrain, "L1-far-background"),
            "profile": _profile(course),
            "favours": [{"role": r, "score": round(v * 100)}
                        for r, v in rep.favours[:4]],
            "notes": rep.profile_notes[:3],
            "koms": len(course.koms),
        }
    return _COURSE_CACHE[key]


def race_json(c: Career, e, index: int, detail: bool = False) -> dict:
    d = {
        "id": e.race_id, "name": e.name, "tier": e.tier.value,
        "day": e.start_day, "days": e.days, "prestige": e.prestige,
        "index": index, "done": index < c.race_index,
        "next": index == c.race_index,
        "badge": art_url(f"race-badge-{index + 1:02d}"),
        "landmark": _terrain_art(e.stages[-1].terrain if e.stages else "coast"),
        "types": [s.stage_type for s in e.stages],
        "total_km": round(sum(s.length_km for s in e.stages)),
    }
    if detail:
        d["stages"] = [stage_detail(e, i, s) for i, s in enumerate(e.stages)]
        d["total_ascent"] = sum(x["ascent"] for x in d["stages"])
        d["entrants"] = [
            {"name": r.name, "overall": r.overall,
             "role_cn": ROLE_CN.get(r.role.value, ""),
             "portrait": art_url(r.art_portrait),
             "team": c.world.team(r.team_id).short_name}
            for r in sorted(c.world.riders, key=lambda x: -x.overall)[:5]]
    return d


def _rider(c: Career, rid: str):
    """查一名车手，查不到返回 None。

    退役的车手会离开世界，但他这个赛季挣到的积分还留在积分表里——
    直接 c.world.rider() 会在休赛期之后炸掉。命令行从来碰不到这一步，
    因为它跑完赛季就不再序列化状态了；界面每一帧都要序列化。
    """
    try:
        return c.world.rider(rid)
    except StopIteration:
        return None


def _team(c: Career, tid: str):
    """同理：车队也会消失（解散、退出）。积分表里却还留着它上赛季的分。"""
    try:
        return c.world.team(tid)
    except StopIteration:
        return None


def standings(c: Career, limit: int = 12) -> list[dict]:
    pts: dict[str, int] = {}
    for rid, p in c._points.items():
        r = _rider(c, rid)
        if r is None:
            continue
        pts[r.team_id] = pts.get(r.team_id, 0) + p
    rows = [(tid, p) for tid, p in sorted(pts.items(), key=lambda x: -x[1])
            if _team(c, tid)]
    out = []
    for i, (tid, p) in enumerate(rows[:limit], start=1):
        t = c.world.team(tid)
        out.append({"rank": i, "id": tid, "name": t.short_name,
                    "full": t.name, "points": p, "color": t.color_primary,
                    "badge": art_url(t.art_badge), "mine": tid == c.team_id})
    if not any(r["mine"] for r in out):
        for i, (tid, p) in enumerate(rows, start=1):
            if tid == c.team_id and _team(c, tid):
                t = c.world.team(tid)
                out.append({"rank": i, "id": tid, "name": t.short_name,
                            "full": t.name, "points": p,
                            "color": t.color_primary,
                            "badge": art_url(t.art_badge), "mine": True})
                break
    return out


def rider_leaders(c: Career, limit: int = 10) -> list[dict]:
    rows = [(rid, p) for rid, p in
            sorted(c._points.items(), key=lambda x: -x[1])
            if _rider(c, rid) and _team(c, _rider(c, rid).team_id)][:limit]
    out = []
    for i, (rid, p) in enumerate(rows, start=1):
        r = c.world.rider(rid)
        out.append({"rank": i, "name": r.name,
                    "team": c.world.team(r.team_id).short_name,
                    "role_cn": ROLE_CN.get(r.role.value, ""),
                    "points": p, "portrait": art_url(r.art_portrait),
                    "mine": r.team_id == c.team_id})
    return out


# 非剧情类的决策没有专属插画，但也不该是一块空白——用对应的经营场景图。
# 「休赛期训练」配一张室内训练馆，「转会市场」配一张转会市场，
# 玩家在弹层出现的第一眼就知道自己在处理哪一类事。
KIND_SCENE = {
    "训练安排": "02-indoor-training",
    "转会决定": "05-transfer-market",
    "赛前指令": "08-team-bus-interior",
}


def _event_art(d) -> str | None:
    """事件 id 是 EV01 这种形式，插画叫 event-01。抠出序号来对。"""
    if d.kind is not DecisionKind.EVENT:
        return art_url(KIND_SCENE.get(d.kind.value, ""))
    eid = d.payload.get("event_id", "")
    num = "".join(ch for ch in eid if ch.isdigit())
    return art_url(f"event-{int(num):02d}") if num else None


def _replay_art(rp: dict | None) -> dict:
    """给回放补上美术路径。

    `game.watch` 只管录，不知道图放在哪儿——它录的是比赛，不是画面。
    路径这件事属于这一层（同一个理由，第四次出现）。
    """
    if not rp:
        return {}
    ter = rp.get("terrain", "coast")
    out = dict(rp)
    out["art"] = {
        "L1": _parallax(ter, "L1-far-background"),
        "L2": _parallax(ter, "L2-mid-background"),
        "L3": _parallax(ter, "L3-near-roadside"),
        "L4": _parallax(ter, "L4-road-tile"),
        "L5": _parallax(ter, "L5-foreground"),
        "poses": [art_url(f"pose-{i:02d}") for i in range(1, 9)],
        "landmark": _terrain_art(ter),
    }
    return out


def _last_race(lr: dict | None) -> dict | None:
    """career 存的是逻辑图名（portrait-12），界面要的是 URL。

    转换放在这一层，而不是让 career 存 URL：生涯层不该知道自己是被
    HTTP 前端还是 Unity 前端读的。这是同一个理由，第三次出现。
    """
    if not lr:
        return None
    out = dict(lr)
    for key in ("top", "mine"):
        out[key] = [{**r, "portrait": art_url(r["portrait"])} for r in lr[key]]
    return out


def state_json(c: Career) -> dict:
    cal = c.calendar
    nxt = c.next_race
    return {
        "season": c.world.season, "day": c.day, "phase": c.phase.value,
        "race_index": c.race_index,
        "playbook": c.playbook, "playbooks": list(PLAYBOOKS),
        "orders": orders_json(),
        "rider_orders": dict(c.rider_orders),
        "squad_size": c.squad_size(),
        "lineup": list(c.lineup),
        "effective_orders": {rid: o.value
                             for rid, o in c.current_orders().items()},
        "team": team_json(c.world, c.team),
        "roster": sorted((rider_json(r, True) for r in c.roster),
                         key=lambda r: -r["overall"]),
        "payroll": sum(r.salary for r in c.roster),
        "calendar": [race_json(c, e, i) for i, e in enumerate(cal)],
        "next_race": race_json(c, nxt, c.race_index, detail=True) if nxt else None,
        "pending": [
            {"kind": d.kind.value, "title": d.title, "detail": d.detail,
             "options": d.options, "default": d.default,
             "art": _event_art(d)}
            for d in c.pending],
        "last_race": _last_race(c.last_race),
        # 回放本身有一两百 KB，不塞进每一次状态刷新——只报个信号，
        # 前端要看的时候单独去取
        "has_replay": c.last_replay is not None,
        "headlines": [{"day": h.day, "text": h.text, "tag": h.tag}
                      for h in reversed(c.season_log[-40:])],
        "season_results": c.season_results,
        "standings": standings(c),
        "rider_leaders": rider_leaders(c),
        "history": c.history,
        "programs": [{"key": p.key, "name": p.name, "blurb": p.blurb,
                      "focus": p.focus} for p in PROGRAMS],
        "camps": [{"key": x.key, "name": x.name, "cost": x.cost_per_rider,
                   "blurb": x.blurb} for x in CAMPS],
        "kv": art_url("key-visual"),
    }


def shortlist_json(c: Career) -> list[dict]:
    out = []
    for l in c.shortlist(8):
        d = rider_json(l.rider)
        d["asking"] = l.asking
        d["from_team"] = (c.world.team(l.rider.team_id).short_name
                          if l.rider.team_id else "自由身")
        out.append(d)
    return out


# --------------------------------------------------------------------------
# 服务端
# --------------------------------------------------------------------------

class Game:
    """一个进程只有一档生涯。单机游戏不需要更复杂的东西。"""

    def __init__(self) -> None:
        self.career: Career | None = None
        self.lock = threading.Lock()

    def start(self, team_id: str, seed: int) -> Career:
        c = new_career(team_id, seed)
        c.attach_db()
        self.career = c
        return c


GAME = Game()


def teams_json() -> list[dict]:
    """开局可选的车队。给的是「你要接手一个什么样的烂摊子」的信息，
    而不是一串数字：预算、阵容厚度、当家车手，玩家据此选难度。"""
    from game.world import World
    w = World.load(ROOT / "data" / "world.json")
    out = []
    for t in sorted(w.teams, key=lambda x: -x.prestige):
        roster = w.roster(t.team_id)
        star = max(roster, key=lambda r: r.overall) if roster else None
        out.append({
            **team_json(w, t),
            "squad": len(roster),
            "avg": round(sum(r.overall for r in roster) / max(1, len(roster))),
            "star": {"name": star.name, "overall": star.overall,
                     "role_cn": ROLE_CN.get(star.role.value, ""),
                     "portrait": art_url(star.art_portrait)} if star else None,
        })
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):            # 安静点，这是个游戏不是服务器
        pass

    # ---- 基础 ----
    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- 路由 ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, APP_HTML.read_bytes(),
                              "text/html; charset=utf-8")
        if path.startswith("/art/"):
            f = (ART / path[5:]).resolve()
            if not str(f).startswith(str(ART.resolve())) or not f.exists():
                return self._send(404, b"", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return self._send(200, f.read_bytes(), ctype)
        if path == "/api/teams":
            return self._json(teams_json())
        if path == "/api/state":
            with GAME.lock:
                if GAME.career is None:
                    return self._json({"started": False})
                return self._json({"started": True, **state_json(GAME.career)})
        if path == "/api/shortlist":
            with GAME.lock:
                return self._json(shortlist_json(GAME.career))
        if path == "/api/history":
            return self._json(history_json())
        if path == "/api/replay":
            with GAME.lock:
                c = GAME.career
                return self._json(_replay_art(c.last_replay) if c else {})
        if path == "/api/hassave":
            return self._json({"exists": SAVE.exists()})
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            b = self._body()
        except Exception:
            b = {}
        with GAME.lock:
            try:
                return self._json(self._act(path, b))
            except Exception as e:      # 让前端看到真实错误，别静默失败
                import traceback
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _act(self, path: str, b: dict) -> dict:
        c = GAME.career
        if path == "/api/new":
            c = GAME.start(b.get("team_id", "T08"), int(b.get("seed", 2026)))
            c.advance()
            return {"started": True, **state_json(c)}
        if path == "/api/load":
            c = Career.load(SAVE)
            c.attach_db()
            GAME.career = c
            return {"started": True, **state_json(c)}
        if c is None:
            return {"started": False}
        if path == "/api/save":
            c.save(SAVE)
            return {"saved": True}
        if path == "/api/advance":
            c.advance()
        elif path == "/api/resolve":
            # 拍板之后不自动往前走。否则玩家选完赛季打法，比赛就直接跑掉了,
            # 他根本没机会看一眼赛道——推进必须永远是玩家自己按的那一下。
            c.resolve(int(b["index"]), int(b["choice"]))
        elif path == "/api/autoresolve":
            c.auto_resolve()
        elif path == "/api/playbook":
            c.set_playbook(b["name"])
        elif path == "/api/order":
            c.set_order(b["rider_id"], b.get("order"))
        elif path == "/api/lineup":
            c.set_lineup(list(b.get("ids") or []))
        elif path == "/api/training":
            c.set_training(b["rider_id"], b["program"], b.get("focus"))
        elif path == "/api/watch":
            # 亲自看下一场：指定要看哪个赛段，然后照常推进
            c.watch_index = int(b.get("stage_index", 0))
            c.advance()
        elif path == "/api/skip":
            # 一路跑到赛季结束，途中所有决策取默认值
            c.play_season(auto=True)
        return {"started": True, **state_json(c)}


def history_json() -> dict:
    from game.records import (career_wins, connect, course_records,
                              dynasty_check, role_balance, summary,
                              wins_by_stage_type)
    conn = GAME.career.db if GAME.career and GAME.career.db else connect()
    s = summary(conn)
    if s["rows"] == 0:
        return {"empty": True}
    return {"empty": False, "summary": s, "hall": career_wins(conn, 15),
            "records": course_records(conn, 15),
            "dynasty": dynasty_check(conn)[:8],
            "roles": role_balance(conn),
            "terrain": wins_by_stage_type(conn),
            "role_cn": ROLE_CN, "type_cn": {**TYPE_CN, "gc": "总成绩"}}


def _serve(port: int) -> tuple[ThreadingHTTPServer, int]:
    """在 port 上起服务；被占了就往后找，最多试 20 个。

    写死一个端口是不行的：上一次没关干净、或者别的程序正好占着 8765，
    玩家看到的就是一串 OSError 堆栈。启动一个单机游戏不该需要玩家
    先学会怎么杀进程。
    """
    last = None
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler), p
        except OSError as e:
            last = e
    raise last


def main() -> None:
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if not (ROOT / "data" / "world.json").exists():
        print("先生成世界：python3 source/game/generate_world.py")
        return
    srv, port = _serve(want)
    url = f"http://127.0.0.1:{port}/"
    if port != want:
        print(f"（{want} 端口被占了，改用 {port}）")
    print(f"模拟自行车队 · 已启动  {url}")
    print("按 Ctrl-C 退出。")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n再见。")


if __name__ == "__main__":
    main()
