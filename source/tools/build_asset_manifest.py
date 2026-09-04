"""扫描 assets，生成「逻辑 id → 实际文件」的清单，并对账游戏数据里的引用。

    python3 source/tools/build_asset_manifest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.assets import ART, write_manifest  # noqa: E402


def main() -> None:
    p, m = write_manifest()
    print(f"资源清单已生成 -> {p.relative_to(ROOT)}（{len(m)} 个逻辑 id）")

    total = len(list(ART.rglob("*.png")))
    print(f"  assets/art 下共 {total} 个 png")

    # 对账 world.json 里的每一个引用
    wp = ROOT / "data" / "world.json"
    if not wp.exists():
        return
    raw = json.loads(wp.read_text(encoding="utf-8"))
    checks: list[tuple[str, str]] = []
    for r in raw["riders"]:
        checks += [("art_portrait", r["art_portrait"]), ("art_body", r["art_body"])]
    for t in raw["teams"]:
        checks.append(("art_badge(车队)", t["art_badge"]))
    for s in raw["sponsors"]:
        checks.append(("art_logo", s["art_logo"]))
    for e in raw["calendar"]:
        if e["art_badge"]:
            checks.append(("art_badge(赛事)", e["art_badge"]))
        for st in e["stages"]:
            checks.append(("art_landmark", st["art_landmark"]))

    uniq = sorted(set(checks))
    missing: dict[str, list[str]] = {}
    for kind, name in uniq:
        if name not in m:
            missing.setdefault(kind, []).append(name)

    print(f"\n游戏数据引用对账：{len(uniq) - sum(len(v) for v in missing.values())}"
          f" / {len(uniq)} 个引用能解析")
    if missing:
        for kind, names in missing.items():
            print(f"  ✗ {kind:<18}{len(names)} 个落空　例：{names[0]}")
    else:
        print("  ✓ 全部命中")

    # 事件插画单独对账（不在 world.json 里）
    from game.events import EVENTS
    bad = [e.art for e in EVENTS if e.art not in m]
    print(f"\n事件插画：{len(EVENTS) - len(bad)} / {len(EVENTS)} 命中"
          + (f"　落空例：{bad[0]}" if bad else ""))


if __name__ == "__main__":
    main()
