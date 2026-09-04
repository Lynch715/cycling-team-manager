"""美术资源解析：逻辑 id → 硬盘上的真实文件。

**为什么需要这一层。** 游戏数据里写的是逻辑名（`sponsor-logo-07`、
`event-12`），出图那边交回来的是 `07_sponsors/logos/sponsor-07.png`、
`09_other/events/event-12-media-scrutiny.png`。两边对不上。

三种解决办法，只有一种是对的：

  改数据 —— 每次出图命名有变化就要改一遍游戏数据，早晚出错
  改文件名 —— 让美术去重命名一百个文件，纯粹的浪费，而且下一批还会错
  **加一层解析** —— 逻辑名保持稳定，实际路径由规则匹配，命名怎么变都不影响

第三种还有个额外好处：出图那边可以在文件名里带描述
（`event-12-media-scrutiny`），人翻文件夹时一眼知道是哪张，
而游戏只认前缀里的序号。**给人看的名字和给程序看的名字本来就该分开。**
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "assets" / "art"
MANIFEST = ROOT / "data" / "asset_manifest.json"

# 逻辑前缀 → 候选目录。按顺序找，第一个命中的算数。
# 一个前缀给多个目录，是为了兼容「同一批资源换过位置」的历史。
SEARCH_DIRS: dict[str, list[str]] = {
    "portrait":     ["01_riders/portraits"],
    "body":         ["01_riders/body_types"],
    "pose":         ["01_riders/action_poses"],
    "emotion":      ["01_riders/emotions"],
    "bike":         ["02_bicycles/side_views"],
    "wheel":        ["02_bicycles/wheelsets"],
    "showcase":     ["02_bicycles/showcase_final", "02_bicycles/showcase_draft"],
    "components":   ["02_bicycles/component_sheets"],
    "scene":        ["03_management_scenes/final_2560x1080",
                     "03_management_scenes"],
    "landmark":     ["04_tracks/landmarks_800x450", "04_tracks/landmarks"],
    "race-badge":   ["04_tracks/race_badges"],
    "leader-jersey": ["04_tracks/leader_jerseys"],
    "sponsor-logo": ["07_sponsors/logos", "07_sponsors"],
    "team-badge":   ["08_team_badges/badges", "08_teams"],
    "event":        ["09_other/events", "09_misc/events"],
    "trophy":       ["09_other/trophies", "09_misc/trophies"],
    "achievements": ["09_other/achievement_sheets"],
    "ui-icons":     ["06_ui/functional_atlases", "06_ui/icon_sheets"],
    "rarity":       ["06_ui/rarity_frames"],
    "loading":      ["06_ui/loading_frames", "06_ui/loading_pages"],
    "empty":        ["06_ui/empty-states", "06_ui/empty_states"],
    "store":        ["09_other/store_screens", "09_misc/store"],
}

# 少数一对一的特例，直接写死比编规则划算
ALIASES: dict[str, str] = {
    "key-visual": "09_other/key-visual-1920x1080.png",
    "app-icon": "09_other/app-icon-1024.png",
    "attribute-icons": "01_riders/attribute-icons-4x4.png",
    "stage-type-icons": "04_tracks/stage-type-icons-4x2.png",
}

_CACHE: dict[str, str] | None = None
_ORD = re.compile(r"(\d+)")


def _ordinal(name: str) -> str | None:
    """从文件名里抠出第一个数字，用来和逻辑名的序号对齐。"""
    m = _ORD.search(name)
    return m.group(1).lstrip("0") or "0" if m else None


def _scan() -> dict[str, str]:
    """扫一遍 assets，建立「逻辑 id → 相对路径」的映射。"""
    out: dict[str, str] = {}

    for logical, rel in ALIASES.items():
        if (ART / rel).exists():
            out[logical] = rel

    for prefix, dirs in SEARCH_DIRS.items():
        for d in dirs:
            folder = ART / d
            if not folder.exists():
                continue
            files = sorted(p for p in folder.glob("*.png") if p.is_file())
            if not files:
                continue
            for p in files:
                stem = p.stem
                rel = str(p.relative_to(ART))
                # 1) 文件名本身就是逻辑名
                out.setdefault(stem, rel)
                # 2) 按序号对齐：sponsor-07.png 也能被 sponsor-logo-07 找到
                num = _ordinal(stem)
                if num is not None:
                    out.setdefault(f"{prefix}-{int(num):02d}", rel)
                # 3) 后缀描述型：event-12-media-scrutiny → event-12
                m = re.match(rf"^{re.escape(prefix)}-(\d+)", stem)
                if m:
                    out.setdefault(f"{prefix}-{int(m.group(1)):02d}", rel)
            break        # 该前缀已在这个目录找到内容，不再看后备目录
    return out


def manifest(refresh: bool = False) -> dict[str, str]:
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    if MANIFEST.exists() and not refresh:
        _CACHE = json.loads(MANIFEST.read_text(encoding="utf-8"))["map"]
    else:
        _CACHE = _scan()
    return _CACHE


def resolve(logical: str) -> str | None:
    """逻辑 id → 相对 assets/art 的路径。找不到返回 None。"""
    return manifest().get(logical)


def path(logical: str) -> Path | None:
    rel = resolve(logical)
    return (ART / rel) if rel else None


def write_manifest() -> tuple[Path, dict[str, str]]:
    m = _scan()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "_说明": [
            "逻辑 id → 实际文件路径（相对 assets/art）。",
            "游戏数据只写逻辑 id，出图那边的命名怎么变都不用改数据。",
            "用 python3 source/tools/build_asset_manifest.py 重新生成。",
        ],
        "count": len(m),
        "map": dict(sorted(m.items())),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    global _CACHE
    _CACHE = m
    return MANIFEST, m
