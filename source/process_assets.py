from pathlib import Path
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assets" / "art"
RESAMPLE = Image.Resampling.LANCZOS


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def fit_rgba(image: Image.Image, size: tuple[int, int], pad=0.04) -> Image.Image:
    image = image.convert("RGBA")
    alpha = image.getchannel("A")
    bbox = alpha.getbbox() or (0, 0, image.width, image.height)
    image = image.crop(bbox)
    target_w = max(1, int(size[0] * (1 - 2 * pad)))
    target_h = max(1, int(size[1] * (1 - 2 * pad)))
    image.thumbnail((target_w, target_h), RESAMPLE)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = image.convert("RGBA")
    scale = max(size[0] / image.width, size[1] / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), RESAMPLE)
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def split_grid(src: Path, cols: int, rows: int, out_dir: Path, names: list[str], size: tuple[int, int], mode="fit", inset=0.0):
    image = Image.open(src).convert("RGBA")
    cell_w = image.width / cols
    cell_h = image.height / rows
    ensure(out_dir)
    for index, name in enumerate(names):
        col = index % cols
        row = index // cols
        left = col * cell_w
        top = row * cell_h
        right = (col + 1) * cell_w
        bottom = (row + 1) * cell_h
        inset_x = cell_w * inset
        inset_y = cell_h * inset
        box = (round(left + inset_x), round(top + inset_y), round(right - inset_x), round(bottom - inset_y))
        cell = image.crop(box)
        final = fit_rgba(cell, size) if mode == "fit" else cover(cell, size)
        final.save(out_dir / name, optimize=True)


def grid_cells(src: Path, cols: int, rows: int) -> list[Image.Image]:
    image = Image.open(src).convert("RGBA")
    cell_w = image.width / cols
    cell_h = image.height / rows
    cells = []
    for index in range(cols * rows):
        col = index % cols
        row = index // cols
        box = (round(col * cell_w), round(row * cell_h), round((col + 1) * cell_w), round((row + 1) * cell_h))
        cells.append(image.crop(box))
    return cells


def combine_icon_halves(src_a: Path, src_b: Path, dst: Path):
    cells = grid_cells(src_a, 4, 2) + grid_cells(src_b, 4, 2)
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    for index, cell in enumerate(cells):
        tile = cover(cell, (256, 256))
        canvas.alpha_composite(tile, ((index % 4) * 256, (index // 4) * 256))
    ensure(dst.parent)
    canvas.save(dst, optimize=True)


def resize_exact(src: Path, dst: Path, size: tuple[int, int], mode="cover"):
    ensure(dst.parent)
    image = Image.open(src)
    final = cover(image, size) if mode == "cover" else fit_rgba(image, size, pad=0)
    final.save(dst, optimize=True)


def make_road_tile(dst: Path, base, edge, dash, texture):
    ensure(dst.parent)
    image = Image.new("RGBA", (512, 256), base)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 512, 18), fill=edge)
    draw.rectangle((0, 238, 512, 256), fill=edge)
    for x in range(-40, 560, 96):
        draw.rounded_rectangle((x, 121, x + 48, 135), radius=7, fill=dash)
    for y in range(28, 232, 24):
        offset = (y * 7) % 32
        for x in range(offset, 512, 32):
            draw.rectangle((x, y, x + 2, y + 2), fill=texture)
    image.save(dst, optimize=True)


def main():
    riders = ART / "01_riders"
    split_grid(riders / "source_atlases/portraits-8x4.png", 8, 4, riders / "portraits",
               [f"portrait-{i:02d}.png" for i in range(1, 33)], (256, 256))
    split_grid(riders / "source_atlases/action-poses-4x2.png", 4, 2, riders / "action_poses",
               [f"pose-{i:02d}.png" for i in range(1, 9)], (512, 512))
    split_grid(riders / "source_atlases/body-types-3x1.png", 3, 1, riders / "body_types",
               ["body-lean.png", "body-standard.png", "body-strong.png"], (600, 900))
    split_grid(riders / "source_atlases/emotions-4x2.png", 4, 2, riders / "emotions",
               [f"emotion-{i:02d}.png" for i in range(1, 9)], (128, 128))

    bikes = ART / "02_bicycles"
    split_grid(bikes / "source_atlases/bicycle-types-2x2.png", 2, 2, bikes / "side_views",
               ["road-bike.png", "climbing-bike.png", "tt-bike.png", "training-bike.png"], (512, 320))
    split_grid(bikes / "source_atlases/wheelsets-2x2.png", 2, 2, bikes / "wheelsets",
               ["wheel-shallow.png", "wheel-medium.png", "wheel-deep.png", "wheel-disc.png"], (256, 256))
    split_grid(bikes / "source_atlases/showcase-2x2-draft.png", 2, 2, bikes / "showcase_draft",
               ["showcase-road-draft.png", "showcase-climbing-draft.png", "showcase-tt-draft.png", "showcase-training-draft.png"], (1200, 800))
    component_sources = [
        bikes / "component_atlases/components-A-frames-4x4.png",
        bikes / "component_atlases/components-B-wheels-drivetrain-4x4.png",
        bikes / "component_atlases/components-C-wearables-4x4.png",
        bikes / "component_atlases/components-D-accessories-4x4.png",
    ]
    sheets = ensure(bikes / "component_sheets")
    sheet_no = 1
    for src in component_sources:
        image = Image.open(src).convert("RGBA")
        row_h = image.height / 4
        for row in range(4):
            if sheet_no > 14:
                break
            strip = image.crop((0, round(row * row_h), image.width, round((row + 1) * row_h)))
            fit_rgba(strip, (1024, 256), pad=0.01).save(sheets / f"components-{sheet_no:02d}-4up.png", optimize=True)
            sheet_no += 1

    scene_dir = ART / "03_management_scenes"
    final_scenes = ensure(scene_dir / "final_2560x1080")
    for src in sorted(scene_dir.glob("[0-9][0-9]-*.png")):
        cover(Image.open(src), (2560, 1080)).save(final_scenes / src.name, optimize=True)

    tracks = ART / "04_tracks"
    final_landmarks = ensure(tracks / "landmarks_800x450")
    for src in sorted((tracks / "landmarks").glob("*.png")):
        cover(Image.open(src), (800, 450)).save(final_landmarks / src.name, optimize=True)
    split_grid(tracks / "source_atlases/optional-landmarks-5x1.png", 5, 1, final_landmarks,
               [f"{i:02d}-optional.png" for i in range(9, 14)], (800, 450), mode="cover")
    split_grid(tracks / "source_atlases/race-badges-3x2.png", 3, 2, tracks / "race_badges",
               [f"race-badge-{i:02d}.png" for i in range(1, 7)], (256, 256))
    split_grid(tracks / "source_atlases/leader-jerseys-5x1.png", 5, 1, tracks / "leader_jerseys",
               [f"leader-jersey-{i:02d}.png" for i in range(1, 6)], (256, 256))

    parallax = ART / "05_race_parallax"
    for theme in ["plains", "alpine", "city", "coast"]:
        folder = parallax / theme
        resize_exact(folder / "L1-far-background.png", folder / "final/L1-far-background.png", (1600, 600), "cover")
        resize_exact(folder / "L2-mid-background.png", folder / "final/L2-mid-background.png", (1600, 500), "fit")
        resize_exact(folder / "L3-near-roadside.png", folder / "final/L3-near-roadside.png", (1600, 400), "fit")
        resize_exact(folder / "L5-foreground.png", folder / "final/L5-foreground.png", (800, 400), "fit")
    make_road_tile(parallax / "plains/final/L4-road-tile.png", (82, 86, 90, 255), (120, 125, 128, 255), (235, 235, 220, 255), (93, 97, 101, 255))
    make_road_tile(parallax / "alpine/final/L4-road-tile.png", (72, 77, 82, 255), (132, 136, 140, 255), (244, 244, 230, 255), (87, 92, 97, 255))
    make_road_tile(parallax / "city/final/L4-road-tile.png", (66, 70, 76, 255), (190, 190, 194, 255), (250, 230, 80, 255), (79, 84, 90, 255))
    make_road_tile(parallax / "coast/final/L4-road-tile.png", (80, 83, 86, 255), (230, 230, 220, 255), (245, 245, 235, 255), (95, 98, 101, 255))
    props = parallax / "props"
    split_grid(props / "spectator-groups-2x2.png", 2, 2, props / "spectators",
               [f"spectators-{i:02d}.png" for i in range(1, 5)], (512, 512))
    split_grid(props / "support-vehicles-2x2.png", 2, 2, props / "vehicles",
               [f"vehicle-{i:02d}.png" for i in range(1, 5)], (512, 320))
    split_grid(props / "ad-boards-2x1.png", 2, 1, props / "ad_boards",
               ["ad-board-low.png", "ad-board-tall.png"], (512, 320))
    split_grid(props / "effects-3x2.png", 3, 2, props / "effects",
               [f"effect-{i:02d}.png" for i in range(1, 7)], (512, 512))
    split_grid(props / "optional-elements-5x2.png", 5, 2, props / "optional_elements",
               [f"optional-element-{i:02d}.png" for i in range(1, 11)], (512, 512))

    ui = ART / "06_ui"
    ui_sources = ui / "source_atlases"
    ui_names = ["training", "management", "equipment", "staff-operations", "race-conditions", "team-tactics", "race-results", "system-navigation"]
    for index, name in enumerate(ui_names, start=1):
        combine_icon_halves(
            ui_sources / f"{index:02d}-{name.split('-')[0]}-A-4x2.png" if index not in (4, 5, 6, 7, 8) else {
                4: ui_sources / "04-staff-A-4x2.png",
                5: ui_sources / "05-race-A-4x2.png",
                6: ui_sources / "06-tactics-A-4x2.png",
                7: ui_sources / "07-results-A-4x2.png",
                8: ui_sources / "08-system-A-4x2.png",
            }[index],
            ui_sources / f"{index:02d}-{name.split('-')[0]}-B-4x2.png" if index not in (4, 5, 6, 7, 8) else {
                4: ui_sources / "04-staff-B-4x2.png",
                5: ui_sources / "05-race-B-4x2.png",
                6: ui_sources / "06-tactics-B-4x2.png",
                7: ui_sources / "07-results-B-4x2.png",
                8: ui_sources / "08-system-B-4x2.png",
            }[index],
            ui / "functional_atlases" / f"ui-{index:02d}-{name}-4x4.png",
        )
    split_grid(ui_sources / "rarity-frames-2x2.png", 2, 2, ui / "rarity_frames",
               ["rarity-common.png", "rarity-rare.png", "rarity-epic.png", "rarity-legendary.png"], (512, 512), mode="cover")
    resize_exact(ui_sources / "loading-rider-6x1.png", ui / "loading-rider-6x1.png", (1536, 256), "cover")
    resize_exact(ui_sources / "empty-roster.png", ui / "empty-states/empty-roster.png", (1024, 1024), "fit")
    resize_exact(ui_sources / "empty-achievements.png", ui / "empty-states/empty-achievements.png", (1024, 1024), "fit")
    for name in ["loading-service-course", "loading-alpine", "loading-sprint"]:
        resize_exact(ui_sources / f"{name}.png", ui / f"loading_pages/{name}.png", (1920, 1080), "cover")

    sponsors = ART / "07_sponsors"
    sponsor_no = 1
    for src in sorted((sponsors / "source_atlases").glob("sponsors-*.png")):
        names = [f"sponsor-{i:02d}.png" for i in range(sponsor_no, sponsor_no + 6)]
        split_grid(src, 3, 2, sponsors / "logos", names, (512, 512), mode="cover")
        sponsor_no += 6

    badges = ART / "08_team_badges"
    badge_no = 1
    for src in sorted((badges / "source_atlases").glob("badges-*.png")):
        names = [f"team-badge-{i:02d}.png" for i in range(badge_no, badge_no + 5)]
        split_grid(src, 5, 1, badges / "badges", names, (512, 512), mode="fit")
        badge_no += 5

    other = ART / "09_other"
    other_sources = other / "source_atlases"
    event_names = [
        "contract-breakthrough", "contract-standoff", "rider-injury", "comeback-training",
        "sponsor-ultimatum", "sponsor-renewal", "team-conflict", "team-reconciliation",
        "surprise-win", "near-miss", "cracked-frame", "emergency-wheel-change",
        "breakout-young-rider", "veteran-retirement", "press-controversy", "fan-community-event",
        "bad-weather-stage", "crash-pileup", "budget-crisis", "trophy-celebration",
    ]
    for sheet_no, src in enumerate(sorted(other_sources.glob("events-*.png"))):
        start = sheet_no * 4
        names = [f"event-{start + i + 1:02d}-{event_names[start + i]}.png" for i in range(4)]
        split_grid(src, 2, 2, other / "events", names, (1200, 675), mode="cover", inset=0.02)
    split_grid(other_sources / "trophies-3x1.png", 3, 1, other / "trophies",
               ["trophy-bronze.png", "trophy-silver.png", "trophy-gold.png"], (512, 512))
    for index, src in enumerate(sorted(other_sources.glob("achievements-*.png")), start=1):
        resize_exact(src, other / f"achievement_sheets/achievements-{index:02d}-3x3.png", (1536, 1536), "cover")
    resize_exact(other_sources / "key-visual.png", other / "key-visual-1920x1080.png", (1920, 1080), "cover")
    resize_exact(other_sources / "app-icon.png", other / "app-icon-1024.png", (1024, 1024), "cover")
    for name in ["store-team", "store-race", "store-season"]:
        resize_exact(other_sources / f"{name}.png", other / f"store_screens/{name}-1920x1080.png", (1920, 1080), "cover")

    for src in sorted((ART / "02_bicycles/showcase").glob("showcase-*.png")):
        resize_exact(src, ART / f"02_bicycles/showcase_final/{src.name}", (1200, 800), "fit")


if __name__ == "__main__":
    main()
