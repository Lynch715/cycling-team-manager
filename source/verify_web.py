"""网页版和本地版是不是同一个游戏——用指纹对账。

    python3 source/verify_web.py            # 打印本地（CPython）指纹
    python3 source/verify_web.py --json     # 只出 JSON，给浏览器端比对用

**为什么要有这个。** 网页版最大的风险从来不是跑不起来，而是「跑起来了，
但数字和本地版对不上」——那等于偷偷有了两套引擎，正是这个项目一开始就
拒绝的那条路。所以光验证「能玩」不够，必须验证「结果一样」。

做法：同一颗种子、同一串 API 调用，跑完一个完整赛季，把最终状态压成一个
指纹。浏览器里跑同一串调用，指纹必须一模一样。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))

import play  # noqa: E402


def call(method: str, path: str, body: dict | None = None) -> dict:
    if method == "GET":
        _, _, data = play.route_get(path)
    else:
        _, _, data = play.route_post(path, body or {})
    return json.loads(data)


def run() -> dict:
    s = call("POST", "/api/new", {"team_id": "T08", "seed": 2026})
    s = call("POST", "/api/resolve", {"index": 0, "choice": 0})
    season0, guard, events = s["season"], 0, 0
    while s["season"] == season0 and guard < 300:
        guard += 1
        if s["pending"]:
            events += len(s["pending"])
            s = call("POST", "/api/autoresolve")
            continue
        s = call("POST", "/api/advance")
    # 休赛期剩下的决定也一并拍掉，阵容才算尘埃落定
    for _ in range(60):
        if not s["pending"]:
            break
        s = call("POST", "/api/autoresolve")

    roster = sorted((r["name"], r["overall"], r["age"]) for r in s["roster"])
    results = [(x.get("race_id") or x.get("id") or "", x.get("winner") or
                (x.get("top") or [{}])[0].get("name", ""))
               for x in (s.get("season_results") or [])]
    core = {"season": s["season"], "budget": s["team"]["budget"],
            "squad": len(roster), "roster": roster,
            "events": events, "races": len(results), "results": results}
    return {"fingerprint": hashlib.sha256(
                json.dumps(core, ensure_ascii=False, sort_keys=True)
                .encode("utf-8")).hexdigest()[:16],
            **core}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = run()
    if a.json:
        print(json.dumps(d, ensure_ascii=False))
        return
    print(f"指纹      {d['fingerprint']}")
    print(f"赛季      {d['season']}")
    print(f"预算      {d['budget']} 万")
    print(f"阵容      {d['squad']} 人")
    print(f"赛事      {d['races']} 场，事务 {d['events']} 件")
    print("前三名车手 " + "、".join(f"{n}({o})" for n, o, _ in d["roster"][:3]))


if __name__ == "__main__":
    main()
