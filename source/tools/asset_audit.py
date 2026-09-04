"""美术资源盘点：清单对账、规格校验、缺口报告。

    python3 source/tools/asset_audit.py                 # 打印报告
    python3 source/tools/asset_audit.py --manifest      # 同时写出 manifest.json

这个工具回答三个问题，每一个都是项目管理里会反复问的：

  1. **还缺什么** —— 254 张的清单里，哪些已经落盘、哪些还没出
  2. **合不合规** —— 尺寸对不对、该透明的有没有透明、命名有没有跑偏
  3. **游戏能不能跑** —— world.json 里引用的每一个 art_* 字段，
     在硬盘上是不是真有对应文件

第三条最关键。数据层已经把美术资源绑死在字段上了，只要有一个引用落空，
前端就会出现一个空洞。与其等跑起来才发现，不如每次出图后跑一遍这个脚本。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "assets" / "art"

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:                                   # pragma: no cover
    HAS_PIL = False

OK, WARN, MISS = "\033[32m✓\033[0m", "\033[33m!\033[0m", "\033[31m✗\033[0m"


# --------------------------------------------------------------------------
# 清单：来自《美术清单_精简版_AI出图.md》
# --------------------------------------------------------------------------

@dataclass
class AssetGroup:
    key: str
    label: str
    folder: str
    pattern: str                # 文件名模板，{i} 为序号
    count: int
    size: tuple[int, int] | None = None
    transparent: bool = False
    tolerance: float = 0.06     # 尺寸容差
    note: str = ""


SPEC: list[AssetGroup] = [
    # 1 车手
    AssetGroup("portrait", "车手头像", "01_riders/portraits",
               "portrait-{i:02d}.png", 32, (256, 256)),
    AssetGroup("pose", "侧视骑行姿势", "01_riders/action_poses",
               "pose-{i:02d}.png", 8, (512, 512), transparent=True),
    AssetGroup("body", "全身立绘", "01_riders/body_types",
               None, 3, (600, 900), transparent=True,
               note="body-lean / body-standard / body-strong"),
    AssetGroup("emotion", "情绪表情", "01_riders/emotions",
               "emotion-{i:02d}.png", 8, (128, 128)),
    AssetGroup("attr_icons", "属性图标合集", "01_riders",
               None, 1, None, note="attribute-icons-4x4.png，切 16 格"),

    # 2 自行车
    AssetGroup("bike_side", "侧视整车", "02_bicycles/side_views",
               None, 4, (512, 320), transparent=True,
               note="road / climbing / tt / training"),
    AssetGroup("wheel", "轮组侧视", "02_bicycles/wheelsets",
               None, 4, (256, 256), transparent=True,
               note="shallow / medium / deep / disc"),
    AssetGroup("showcase", "3/4 展示图", "02_bicycles/showcase_final",
               None, 4, (1200, 800)),
    AssetGroup("components", "部件图标合集", "02_bicycles/component_sheets",
               "components-{i:02d}-4up.png", 14, None, note="每张切 4 格 = 56 个"),

    # 3 经营场景
    AssetGroup("scene", "经营场景", "03_management_scenes/final_2560x1080",
               None, 10, (2560, 1080)),

    # 4 赛道
    AssetGroup("landmark", "赛道地标", "04_tracks/landmarks_800x450",
               None, 8, (800, 450), note="另有 5 张备选"),
    AssetGroup("race_badge", "赛事徽章", "04_tracks/race_badges",
               "race-badge-{i:02d}.png", 6, (256, 256), transparent=True),
    AssetGroup("jersey", "领骑衫", "04_tracks/leader_jerseys",
               "leader-jersey-{i:02d}.png", 5, (256, 256), transparent=True),
    AssetGroup("stage_icons", "赛段类型图标合集", "04_tracks",
               None, 1, None, note="stage-type-icons-4x2.png，切 8 格"),

    # 5 竞速卷轴
    AssetGroup("parallax_alpine", "高山主题卷轴", "05_race_parallax/alpine/final",
               None, 5, None, note="L1-L5 五层"),
    AssetGroup("parallax_city", "城市主题卷轴", "05_race_parallax/city/final",
               None, 5, None, note="L1-L5 五层"),
    AssetGroup("parallax_coast", "海岸主题卷轴", "05_race_parallax/coast/final",
               None, 5, None, note="L1-L5 五层"),
    AssetGroup("parallax_plains", "平原主题卷轴", "05_race_parallax/plains/final",
               None, 5, None, note="L1-L5 五层"),
    AssetGroup("spectators", "观众群", "05_race_parallax/props/spectators",
               "spectators-{i:02d}.png", 4, None, transparent=True),
    AssetGroup("vehicles", "随行车辆", "05_race_parallax/props/vehicles",
               "vehicle-{i:02d}.png", 4, None, transparent=True),
    AssetGroup("effects", "特效贴图", "05_race_parallax/props/effects",
               "effect-{i:02d}.png", 6, None, transparent=True),
    AssetGroup("ad_boards", "路边广告牌", "05_race_parallax/props/ad_boards",
               None, 2, None, transparent=True),
    AssetGroup("optional_elem", "备用主题元素",
               "05_race_parallax/props/optional_elements",
               "optional-element-{i:02d}.png", 10, None, transparent=True),

    # 6 UI —— 尚未开工
    AssetGroup("ui_icons", "功能图标合集", "06_ui/functional_atlases",
               None, 8, None, note="每张 4×4 = 128 个图标"),
    AssetGroup("ui_rarity", "稀有度品质框", "06_ui/rarity_frames",
               None, 4, None, transparent=True),
    AssetGroup("ui_loading", "Loading 骑行小人", "06_ui/loading_frames",
               "loading-{i:02d}.png", 6, None, transparent=True,
               note="由 loading-rider-6x1.png 切出的 6 帧"),
    AssetGroup("ui_empty", "空状态插画", "06_ui/empty-states",
               None, 2, None),
    AssetGroup("ui_splash", "加载页插画", "06_ui/loading_pages",
               None, 3, None),

    # 7 赞助商
    AssetGroup("sponsor", "赞助商 Logo", "07_sponsors/logos",
               None, 24, (512, 512), transparent=True),

    # 8 车队徽章
    AssetGroup("team_badge", "车队徽章", "08_team_badges/badges",
               "team-badge-{i:02d}.png", 20, (512, 512), transparent=True),

    # 9 其他
    AssetGroup("event_art", "事件剧情插画", "09_other/events",
               None, 20, (1200, 675)),
    AssetGroup("trophy", "奖杯", "09_other/trophies",
               None, 3, None, transparent=True),
    AssetGroup("achievement", "成就图标合集", "09_other/achievement_sheets",
               "achievements-{i:02d}-3x3.png", 4, None, note="每张切 9 格 = 36 个"),
    AssetGroup("kv", "主视觉 KV", "09_other", "key-visual-1920x1080.png", 1, None),
    AssetGroup("appicon", "App 图标", "09_other", "app-icon-1024.png", 1, None),
    AssetGroup("store", "商店截图底板", "09_other/store_screens",
               None, 3, None),
]


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------

@dataclass
class FileInfo:
    path: str
    width: int = 0
    height: int = 0
    has_alpha: bool = False
    kb: int = 0


@dataclass
class GroupReport:
    key: str
    label: str
    expected: int
    found: int
    files: list[FileInfo] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def missing(self) -> int:
        return max(0, self.expected - self.found)

    @property
    def status(self) -> str:
        if self.found == 0:
            return "未开工"
        if self.missing:
            return "进行中"
        return "已完成" if not self.issues else "需返工"


def inspect(path: Path) -> FileInfo:
    info = FileInfo(path=str(path.relative_to(ROOT)),
                    kb=int(path.stat().st_size / 1024))
    if HAS_PIL:
        with Image.open(path) as im:
            info.width, info.height = im.size
            if im.mode in ("RGBA", "LA"):
                alpha = im.getchannel("A")
                info.has_alpha = alpha.getextrema()[0] < 255
    return info


def audit_group(group: AssetGroup) -> GroupReport:
    folder = ART / group.folder
    report = GroupReport(group.key, group.label, group.count, 0,
                         note=group.note)
    if not folder.exists():
        return report

    if group.pattern and "{i" not in group.pattern:
        files = [folder / group.pattern]
        files = [f for f in files if f.exists()]
    elif group.pattern:
        files = [folder / group.pattern.format(i=i)
                 for i in range(1, group.count + 1)]
        files = [f for f in files if f.exists()]
    else:
        files = sorted(p for p in folder.glob("*.png") if p.is_file())
        # 只统计目录直属文件，子目录属于别的组
        files = [f for f in files if f.parent == folder]

    report.found = len(files)
    for f in files[:200]:
        info = inspect(f)
        report.files.append(info)
        if group.size and info.width:
            tw, th = group.size
            if (abs(info.width - tw) / tw > group.tolerance
                    or abs(info.height - th) / th > group.tolerance):
                report.issues.append(
                    f"{f.name} 尺寸 {info.width}×{info.height}，应为 {tw}×{th}")
        if group.transparent and info.width and not info.has_alpha:
            report.issues.append(f"{f.name} 缺少透明底")
    return report


# --------------------------------------------------------------------------
# 与游戏数据对账
# --------------------------------------------------------------------------

def audit_data_references(world_path: Path) -> list[tuple[str, str, bool]]:
    """检查 world.json 里每一个美术引用能不能解析到真实文件。

    走 game.assets 的解析层，而不是自己拼路径——出图那边的目录和命名
    变过一次之后，硬编码路径的对账会全部误报「落空」，而实际上文件就在
    隔壁文件夹躺着。这正是第一版发生的事：报告说 44 个引用落空、
    99 张没开工，真实情况是美术早就交齐了。
    """
    sys.path.insert(0, str(ROOT / "source"))
    from game.assets import write_manifest

    if not world_path.exists():
        return []
    _, amap = write_manifest()
    raw = json.loads(world_path.read_text(encoding="utf-8"))

    refs: list[tuple[str, str]] = []
    for r in raw["riders"]:
        refs += [("art_portrait", r["art_portrait"]), ("art_body", r["art_body"])]
    for tm in raw["teams"]:
        refs.append(("art_badge(车队)", tm["art_badge"]))
    for s in raw["sponsors"]:
        refs.append(("art_logo", s["art_logo"]))
    for e in raw["calendar"]:
        if e["art_badge"]:
            refs.append(("art_badge(赛事)", e["art_badge"]))
        for st in e["stages"]:
            refs.append(("art_landmark", st["art_landmark"]))

    return [(kind, name, name in amap) for kind, name in sorted(set(refs))]


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true", help="写出 manifest.json")
    ap.add_argument("--world", default="data/world.json")
    args = ap.parse_args()

    if not HAS_PIL:
        print("提示：未安装 Pillow，只统计数量，不校验尺寸与透明底\n")

    reports = [audit_group(g) for g in SPEC]
    total_expected = sum(r.expected for r in reports)
    total_found = sum(min(r.found, r.expected) for r in reports)

    print("=" * 78)
    print(f"美术资源盘点  ·  {total_found} / {total_expected} 张"
          f"（{total_found / total_expected:.0%}）")
    print("=" * 78)
    print(f"\n{'模块':<16}{'已出/应出':<12}{'状态':<8}{'问题'}")
    print("-" * 78)
    for r in reports:
        mark = OK if r.status == "已完成" else (
            MISS if r.status == "未开工" else WARN)
        issue = f"{len(r.issues)} 项规格问题" if r.issues else ""
        print(f"{mark} {r.label:<15}{r.found:>3}/{r.expected:<8}"
              f"{r.status:<8}{issue}")

    todo = [r for r in reports if r.missing]
    if todo:
        print(f"\n{'-' * 78}\n还需出图 {sum(r.missing for r in todo)} 张：")
        for r in todo:
            note = f"（{r.note}）" if r.note else ""
            print(f"  {r.label:<16}还缺 {r.missing:>3} 张 {note}")

    bad = [r for r in reports if r.issues]
    if bad:
        print(f"\n{'-' * 78}\n规格问题：")
        for r in bad:
            for issue in r.issues[:4]:
                print(f"  {r.label}：{issue}")
            if len(r.issues) > 4:
                print(f"  {r.label}：……另有 {len(r.issues) - 4} 项")

    checks = audit_data_references(ROOT / args.world)
    if checks:
        ok = sum(1 for _, _, e in checks if e)
        print(f"\n{'-' * 78}\n游戏数据引用对账：{ok} / {len(checks)} 个引用能找到文件")
        broken: dict[str, int] = {}
        for kind, _, exists in checks:
            if not exists:
                broken[kind] = broken.get(kind, 0) + 1
        for kind, n in sorted(broken.items(), key=lambda x: -x[1]):
            print(f"  {MISS} {kind:<18}{n} 个引用落空")
        if not broken:
            print(f"  {OK} 全部命中，前端可以直接按 world.json 加载")

    if args.manifest:
        manifest = {
            "generated_from": str(ART.relative_to(ROOT)),
            "total_expected": total_expected,
            "total_found": total_found,
            "groups": [
                {**{k: v for k, v in asdict(r).items() if k != "files"},
                 "files": [asdict(f) for f in r.files]}
                for r in reports
            ],
        }
        out = ROOT / "data" / "asset_manifest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nmanifest 已写出 -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
