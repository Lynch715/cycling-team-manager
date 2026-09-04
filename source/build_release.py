from pathlib import Path
from shutil import copy2


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assets" / "art"
RELEASE = ROOT / "release" / "模拟自行车队经理_美术资源"


def copy_file(src: Path, category: str, group: str | None = None, name: str | None = None):
    if not src.is_file():
        raise FileNotFoundError(src)
    dst_dir = RELEASE / category
    if group:
        dst_dir /= group
    dst_dir.mkdir(parents=True, exist_ok=True)
    copy2(src, dst_dir / (name or src.name))


def copy_dir(src_dir: Path, category: str, group: str | None = None, pattern: str = "*.png"):
    for src in sorted(src_dir.glob(pattern)):
        copy_file(src, category, group)


def main():
    if RELEASE.exists():
        raise SystemExit(f"Release directory already exists; move it aside before rebuilding: {RELEASE}")

    copy_dir(ART / "01_riders/portraits", "01_车手_52", "01_头像_32")
    copy_dir(ART / "01_riders/action_poses", "01_车手_52", "02_动作姿势_8")
    copy_dir(ART / "01_riders/body_types", "01_车手_52", "03_体型_3")
    copy_dir(ART / "01_riders/emotions", "01_车手_52", "04_表情_8")
    copy_file(ART / "01_riders/attribute-icons-4x4.png", "01_车手_52", "05_属性图标_1")

    copy_dir(ART / "02_bicycles/side_views", "02_自行车_26", "01_侧视图_4")
    copy_dir(ART / "02_bicycles/showcase_final", "02_自行车_26", "02_三分之四展示图_4")
    copy_dir(ART / "02_bicycles/wheelsets", "02_自行车_26", "03_轮组_4")
    copy_dir(ART / "02_bicycles/component_sheets", "02_自行车_26", "04_配件图集_14")

    copy_dir(ART / "03_management_scenes/final_2560x1080", "03_管理场景_10")

    copy_dir(ART / "04_tracks/landmarks_800x450", "04_赛道_25", "01_地标_13")
    copy_file(ART / "04_tracks/stage-type-icons-4x2.png", "04_赛道_25", "02_赛段类型_1")
    copy_dir(ART / "04_tracks/race_badges", "04_赛道_25", "03_赛事徽章_6")
    copy_dir(ART / "04_tracks/leader_jerseys", "04_赛道_25", "04_领骑衫_5")

    for index, theme in enumerate(["plains", "alpine", "city", "coast"], start=1):
        copy_dir(ART / f"05_race_parallax/{theme}/final", "05_比赛与视差_47", f"{index:02d}_{theme}_5")
    props = ART / "05_race_parallax/props"
    copy_dir(props / "spectators", "05_比赛与视差_47", "05_观众_4")
    copy_dir(props / "vehicles", "05_比赛与视差_47", "06_后勤车辆_4")
    copy_file(props / "race-facilities-3x2.png", "05_比赛与视差_47", "07_赛事设施图集_1")
    copy_dir(props / "ad_boards", "05_比赛与视差_47", "08_广告牌_2")
    copy_dir(props / "effects", "05_比赛与视差_47", "09_特效_6")
    copy_dir(props / "optional_elements", "05_比赛与视差_47", "10_可选元素_10")

    copy_dir(ART / "06_ui/functional_atlases", "06_UI_18", "01_功能图集_8")
    copy_dir(ART / "06_ui/rarity_frames", "06_UI_18", "02_稀有度边框_4")
    copy_file(ART / "06_ui/loading-rider-6x1.png", "06_UI_18", "03_加载骑手_1")
    copy_dir(ART / "06_ui/empty-states", "06_UI_18", "04_空状态_2")
    copy_dir(ART / "06_ui/loading_pages", "06_UI_18", "05_加载页_3")

    copy_dir(ART / "07_sponsors/logos", "07_赞助商标志_24")
    copy_dir(ART / "08_team_badges/badges", "08_车队徽章_20")

    copy_dir(ART / "09_other/events", "09_其他_32", "01_事件插画_20")
    copy_dir(ART / "09_other/trophies", "09_其他_32", "02_奖杯_3")
    copy_dir(ART / "09_other/achievement_sheets", "09_其他_32", "03_成就图集_4")
    copy_file(ART / "09_other/key-visual-1920x1080.png", "09_其他_32", "04_主视觉_1")
    copy_file(ART / "09_other/app-icon-1024.png", "09_其他_32", "05_App图标_1")
    copy_dir(ART / "09_other/store_screens", "09_其他_32", "06_商店展示底图_3")

    expected = {
        "01_车手_52": 52,
        "02_自行车_26": 26,
        "03_管理场景_10": 10,
        "04_赛道_25": 25,
        "05_比赛与视差_47": 47,
        "06_UI_18": 18,
        "07_赞助商标志_24": 24,
        "08_车队徽章_20": 20,
        "09_其他_32": 32,
    }
    total = 0
    for category, target in expected.items():
        actual = len(list((RELEASE / category).rglob("*.png")))
        if actual != target:
            raise SystemExit(f"Count mismatch: {category}: expected {target}, got {actual}")
        print(f"PASS {category}: {actual}")
        total += actual
    if total != 254:
        raise SystemExit(f"Total mismatch: expected 254, got {total}")
    print(f"PASS TOTAL: {total}")


if __name__ == "__main__":
    main()
