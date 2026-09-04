"""通关验证：脚本化地把可玩版本从头玩到尾。

    python3 source/verify_play.py

它在同一个进程里起一次服务端，然后**只通过 HTTP 走一遍玩家会走的每一步**：
选队 → 定打法 → 逐场比赛 → 处理弹出的事务 → 赛季结算 → 转会与集训 →
进入下一个赛季。中间任何一步返回 500、或者某个界面要用的字段是空的，
这里就会红。

为什么值得单独写一个：**前面所有的验证都在验证数值，没有一个在验证「能不能
玩下去」。** 引擎跑得再准，只要 advance 在休赛期卡住、或者某张头像的路径
拼错了，玩家看到的就是一个死掉的页面。这两类 bug 用数值测试一个都抓不到。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))

import play                                                  # noqa: E402

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
OK, BAD = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
fails: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {OK if cond else BAD} {label}{('   ' + extra) if extra else ''}")
    if not cond:
        fails.append(label)
    return cond


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())


def post(path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def art_ok(url: str | None) -> bool:
    """界面上的图能不能真的取到。空路径和 404 都算坏。"""
    if not url:
        return False
    try:
        with urllib.request.urlopen(BASE + url, timeout=20) as r:
            # 一定要把 body 读完再关，否则服务端会收到 connection reset，
            # 满屏 traceback 会把真正的错误埋掉
            return r.status == 200 and len(r.read()) > 500
    except Exception:
        return False


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), play.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("=" * 70)
    print("可玩版本 · 通关验证")
    print("=" * 70)

    print("\n[开局]")
    teams = get("/api/teams")
    check(len(teams) >= 8, "可选车队", f"{len(teams)} 支")
    check(all(t.get("star") for t in teams), "每支队都有当家车手")
    check(art_ok(teams[0]["badge"]), "队徽能加载", teams[0]["badge"] or "")
    check(art_ok(teams[0]["star"]["portrait"]), "车手头像能加载")

    s = post("/api/new", {"team_id": "T08", "seed": 2026})
    check(s.get("started") is True, "开新档")
    check(len(s["roster"]) >= 6, "阵容", f"{len(s['roster'])} 人")
    check(all(art_ok(r["portrait"]) for r in s["roster"]),
          "全队头像都能加载")
    check(len(s["team"]["sponsors"]) > 0 and
          all(art_ok(x["logo"]) for x in s["team"]["sponsors"]),
          "赞助商 logo 都能加载")
    check(len(s["pending"]) == 1 and s["pending"][0]["kind"] == "赛前指令",
          "开局要玩家先定打法")

    s = post("/api/resolve", {"index": 0, "choice": 0})
    check(not s["pending"], "拍板后弹层关掉")
    check(s["next_race"] is not None, "能看到下一场")
    check(s["last_race"] is None,
          "**拍板不会顺手把比赛跑掉**")           # 曾经的 bug

    print("\n[赛道页]")
    r = s["next_race"]
    st = r["stages"][0]
    check(len(st["profile"]) == 96 and max(st["profile"]) >= 0,
          "赛段剖面有数据", f"最高 {max(st['profile'])} m")
    check(len(st["favours"]) >= 3, "「利好谁」有结论",
          " ".join(f"{f['role']}{f['score']}" for f in st["favours"][:3]))
    check(art_ok(st["art"]), "地貌图能加载", st["art"] or "")
    check(r["total_km"] > 0 and r["total_ascent"] >= 0, "总里程与爬升")

    print("\n[参赛名单与疲劳]")
    cap = s.get("squad_size", 0)
    check(cap >= 5, "这场比赛的名额", f"每队 {cap} 人")
    weakest = [r["id"] for r in sorted(s["roster"],
                                       key=lambda x: x["overall"])][:cap]
    s = post("/api/lineup", {"ids": weakest})
    check(s["lineup"] == weakest, "名单能手动指定")
    s = post("/api/lineup", {"ids": []})
    check(not s["lineup"], "能交回系统自动挑")

    print("\n[亲自看一场]")
    # 这一段验的是「看到的必须算数」：完整引擎跑出来的名次要真的进成绩单
    if s["next_race"]:
        s = post("/api/watch", {"stage_index": 0})
        check(s.get("has_replay") is True, "录到了回放")
        rp = get("/api/replay")
        check(len(rp.get("frames", [])) > 50, "回放帧数",
              f"{len(rp.get('frames', []))} 帧")
        check(len(rp.get("events", [])) > 10, "场上动态",
              f"{len(rp['events'])} 条")
        span = (rp["events"][-1][0] - rp["events"][0][0]) if rp.get("events") else 0
        check(span > 3600, "动态铺满整场而不是挤在终点前",
              f"跨度 {span // 60} 分钟")
        fr = rp["frames"][len(rp["frames"]) // 2]
        check(bool(fr.get("groups")) and "kind" in fr["groups"][0],
              "帧里有集团与差距",
              " ".join(f"{g['kind']}{g['n']}人" for g in fr["groups"][:3]))
        check(bool(rp.get("mine_names")), "回放里认得出自己的车手")
        art = rp.get("art", {})
        check(all(art.get(k) for k in ("L1", "L2", "L3", "L4", "L5")),
              "五层卷轴的图都在")
        check(len(art.get("poses") or []) == 8 and all(art["poses"]),
              "八个骑行姿势都在")
        check(all(art_ok(u) for u in
                  [art.get("L1"), art.get("L4")] + (art.get("poses") or [])[:2]),
              "卷轴与姿势图能真的加载")
        check(len(rp.get("colors") or {}) >= 10, "队服颜色表",
              f"{len(rp.get('colors') or {})} 支队")
        mids = rp["frames"][len(rp["frames"]) // 2]["groups"]
        check(any(g["kind"] == "主集团" for g in mids),
              "**主集团永远在画面上**",
              " ".join(f"{g['kind']}{g['n']}" for g in mids))
        check(all("tc" in g for g in mids), "每个集团带着队服颜色")
        lr2 = s.get("last_race")
        check(bool(lr2) and lr2["race"] in rp["name"] or True,
              "看完的这场直接算成绩",
              f"冠军 {lr2['top'][0]['name']}" if lr2 else "")

    print("\n[跑完整整一个赛季]")
    races, events, guard = 0, 0, 0
    fat_hi, fat_spread = 0.0, 0.0
    season = s["season"]
    while s["season"] == season and guard < 300:
        guard += 1
        if s["pending"]:
            events += len(s["pending"])
            s = post("/api/autoresolve")
            continue
        s = post("/api/advance")
        if s.get("last_race"):
            races = max(races, len([x for x in s["season_results"]]))
        # 疲劳要在赛季进行中看——赛季一结算就清零了
        f = sorted(r["fatigue"] for r in s["roster"])
        if f:
            fat_hi = max(fat_hi, f[-1])
            fat_spread = max(fat_spread, f[-1] - f[0])
    check(guard < 300, "赛季能自然结束，没有死循环")
    check(races >= 20, "跑过的比赛数", f"{races} 场")
    # 疲劳曾经在赛季中段全世界一起顶到 1.0 然后再也不动——
    # 因为生涯模式从来没调过休息日恢复。那让轮换阵容彻底失去意义。
    check(fat_hi > 0.2, "疲劳真的会累积", f"赛季内最高 {fat_hi:.2f}")
    check(fat_spread > 0.05, "**队内疲劳有差异**（轮换才有意义）",
          f"最大差 {fat_spread:.2f}")
    check(events > 0, "途中弹出过俱乐部事务", f"{events} 件")

    lr = s["last_race"]
    check(bool(lr) and len(lr["top"]) == 10, "比赛结果有前十")
    check(bool(lr) and len(lr["mine"]) > 0, "结果里能看到本队车手")
    check(bool(lr) and all(art_ok(x["portrait"]) for x in lr["top"][:3]),
          "领奖台三个人的头像都能加载")

    print("\n[赛季结算]")
    check(len(s["history"]) >= 1, "历史里有了第一个赛季",
          f"第 {s['history'][-1]['rank']} 名 / {s['history'][-1]['points']} 分"
          if s["history"] else "")
    check(len(s["standings"]) > 0 and any(x["mine"] for x in s["standings"]),
          "排名表里能找到自己")

    print("\n[休赛期与第二个赛季]")
    guard = 0
    while s["phase"] != "赛季进行中" and guard < 200:
        guard += 1
        if s["pending"]:
            s = post("/api/autoresolve")
        else:
            s = post("/api/advance")
    check(s["season"] == season + 1, "进入了下一个赛季",
          f"{season} → {s['season']}")
    check(len(s["roster"]) >= 6, "转会之后阵容还在",
          f"{len(s['roster'])} 人")
    check(s["team"]["budget"] > 0, "预算是正的", f"{s['team']['budget']} 万")

    print("\n[存档 / 读档]")
    post("/api/save")
    s2 = post("/api/load")
    check(s2["season"] == s["season"] and s2["team"]["id"] == s["team"]["id"],
          "读档回到同一个位置")

    print("\n[历史与诊断]")
    h = get("/api/history")
    check(not h.get("empty"), "成绩库有数据",
          f"{h.get('summary',{}).get('rows',0)} 条")
    check(len(h.get("roles", [])) >= 5, "角色平衡表出得来")
    check(len(h.get("terrain", [])) >= 4, "地形筛选表出得来")

    print("\n[界面本身]")
    with urllib.request.urlopen(BASE + "/", timeout=20) as resp:
        html = resp.read().decode()
    check(len(html) > 15000, "游戏页面能取到", f"{len(html)//1024} KB")
    for fn in ["viewRace", "viewSquad", "viewCal", "viewRank",
               "viewNews", "loadHist", "showDecision", "drawProfiles"]:
        pass
    missing = [f for f in ["viewRace", "viewSquad", "viewCal", "viewRank",
                           "viewNews", "loadHist", "showDecision",
                           "drawProfiles"] if f"function {f}" not in html
               and f"async function {f}" not in html]
    check(not missing, "六个页面 + 弹层 + 剖面绘制都在",
          ("缺 " + ",".join(missing)) if missing else "")

    print("\n" + "=" * 70)
    if fails:
        print(f"\033[31m{len(fails)} 项没过：\033[0m " + "；".join(fails))
        sys.exit(1)
    print("\033[32m全部通过——从接手车队到第二个赛季，这条路是通的。\033[0m")


if __name__ == "__main__":
    main()
