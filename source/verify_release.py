from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release" / "模拟自行车队经理_美术资源"
EXPECTED_COUNTS = {
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


def expected_size(rel: Path):
    text = rel.as_posix()
    if "01_头像_32" in text:
        return (256, 256)
    if "02_动作姿势_8" in text:
        return (512, 512)
    if "03_体型_3" in text:
        return (600, 900)
    if "04_表情_8" in text:
        return (128, 128)
    if "01_侧视图_4" in text:
        return (512, 320)
    if "02_三分之四展示图_4" in text:
        return (1200, 800)
    if "03_轮组_4" in text:
        return (256, 256)
    if "04_配件图集_14" in text:
        return (1024, 256)
    if rel.parts[0] == "03_管理场景_10":
        return (2560, 1080)
    if "01_地标_13" in text:
        return (800, 450)
    if "03_赛事徽章_6" in text or "04_领骑衫_5" in text:
        return (256, 256)
    if rel.parts[0] == "05_比赛与视差_47":
        if rel.name == "L1-far-background.png":
            return (1600, 600)
        if rel.name == "L2-mid-background.png":
            return (1600, 500)
        if rel.name == "L3-near-roadside.png":
            return (1600, 400)
        if rel.name == "L4-road-tile.png":
            return (512, 256)
        if rel.name == "L5-foreground.png":
            return (800, 400)
        if "05_观众_4" in text or "09_特效_6" in text or "10_可选元素_10" in text:
            return (512, 512)
        if "06_后勤车辆_4" in text or "08_广告牌_2" in text:
            return (512, 320)
    if "01_功能图集_8" in text:
        return (1024, 1024)
    if "02_稀有度边框_4" in text:
        return (512, 512)
    if "03_加载骑手_1" in text:
        return (1536, 256)
    if "04_空状态_2" in text:
        return (1024, 1024)
    if "05_加载页_3" in text:
        return (1920, 1080)
    if rel.parts[0] in ("07_赞助商标志_24", "08_车队徽章_20"):
        return (512, 512)
    if "01_事件插画_20" in text:
        return (1200, 675)
    if "02_奖杯_3" in text:
        return (512, 512)
    if "03_成就图集_4" in text:
        return (1536, 1536)
    if "04_主视觉_1" in text or "06_商店展示底图_3" in text:
        return (1920, 1080)
    if "05_App图标_1" in text:
        return (1024, 1024)
    return None


def requires_transparency(rel: Path):
    if rel.parts[0] in ("01_车手_52", "02_自行车_26", "07_赞助商标志_24", "08_车队徽章_20"):
        return True
    return rel.parts[0] == "06_UI_18" and "05_加载页_3" not in rel.as_posix()


def main():
    failures = []
    images = sorted(RELEASE.rglob("*.png"))
    if len(images) != 254:
        failures.append(f"total count: expected 254, got {len(images)}")
    for category, target in EXPECTED_COUNTS.items():
        actual = len(list((RELEASE / category).rglob("*.png")))
        if actual != target:
            failures.append(f"{category}: expected {target}, got {actual}")

    hashes = defaultdict(list)
    transparent_count = 0
    for path in images:
        rel = path.relative_to(RELEASE)
        try:
            with Image.open(path) as image:
                image.load()
                size = image.size
                rgba = image.convert("RGBA")
                alpha_extrema = rgba.getchannel("A").getextrema()
        except Exception as exc:
            failures.append(f"decode failed: {rel}: {exc}")
            continue
        wanted = expected_size(rel)
        if wanted is not None and size != wanted:
            failures.append(f"size mismatch: {rel}: expected {wanted}, got {size}")
        has_transparency = alpha_extrema[0] < 255
        transparent_count += int(has_transparency)
        if requires_transparency(rel) and not has_transparency:
            failures.append(f"missing transparency: {rel}")
        hashes[sha256(path.read_bytes()).hexdigest()].append(rel)

    duplicates = [paths for paths in hashes.values() if len(paths) > 1]
    if duplicates:
        for paths in duplicates:
            failures.append("duplicate content: " + ", ".join(map(str, paths)))

    print(f"PNG files: {len(images)}")
    print(f"Decoded: {len(images) - sum('decode failed' in item for item in failures)}")
    print(f"Files with actual transparent pixels: {transparent_count}")
    print(f"Duplicate-content groups: {len(duplicates)}")
    if failures:
        print("FAIL")
        for item in failures:
            print(f"- {item}")
        raise SystemExit(1)
    print("PASS: counts, decoding, dimensions, transparency rules, and duplicate hashes")


if __name__ == "__main__":
    main()
