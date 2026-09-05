"""把网页版打包到 play/ —— GitHub Pages 直接发这个目录。

    python3 source/tools/build_web.py            # 只打包代码
    python3 source/tools/build_web.py --art      # 顺便把美术转成 WebP
    python3 source/tools/build_web.py --local-pyodide  # 用本地运行时（离线自测）

产物：
    play/index.html       play_app.html + 引导层，前端源码一个字没改
    play/engine.zip       sim/ + game/ + play.py + web/ + 种子数据
    play/art/             美术，WebP
    play/art_index.json   有哪些图——浏览器里查不了文件系统，只能查清单

**为什么美术要单独转一遍而不是直接用 assets/art。** 那边 209 张引用图共
122.7 MB，PNG 且分辨率远超界面实际显示尺寸。原样发上去每个访客都要拉
120 MB，而 git 还得永久背着它们。转 WebP + 限长边，体积掉一个数量级，
肉眼看不出差别。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "play"
APP = ROOT / "source" / "tools" / "play_app.html"
PRELUDE = ROOT / "source" / "web" / "prelude.html"

CDN_PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js"
LOCAL_PYODIDE = "pyodide/pyodide.js"

# 引擎需要的种子数据。records.db 之类的运行时产物一概不带——
# 浏览器里那是一局游戏自己的事，不该由发布包决定。
DATA_FILES = ["world.json", "climbs.json", "calendar.json",
              "asset_manifest.json"]
DATA_DIRS = ["courses"]

# 界面上真正会显示的图，长边限制。数字来自 play_app.html 里的实际用法：
# 头像最大 96px、徽章 64px、地貌图 800x450、场景是整屏背景。
MAX_EDGE = {
    "01_riders/portraits": 256, "01_riders/emotions": 256,
    "01_riders/body_types": 512, "01_riders/action_poses": 512,
    "04_tracks/landmarks_800x450": 800, "04_tracks/race_badges": 192,
    "04_tracks/leader_jerseys": 192,
    "07_sponsors": 256, "08_team_badges": 256,
    "03_management_scenes": 1600, "05_race_parallax": 1280,
    "09_other": 800, "06_ui": 512,
}
DEFAULT_EDGE = 640


def build_index(pyodide_url: str) -> str:
    app = APP.read_text(encoding="utf-8")
    prelude = (PRELUDE.read_text(encoding="utf-8")
               .replace("__PYODIDE_URL__", pyodide_url)
               .replace("__PYODIDE_CDN__", CDN_PYODIDE))
    marker = "<script>"
    i = app.index(marker)
    return app[:i] + prelude + "\n" + app[i:]


def build_zip(dest: Path) -> int:
    src = ROOT / "source"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(src / "play.py", "source/play.py")
        # 对账脚本也带上：网页版最该被怀疑的就是「数字和本地版对不上」，
        # 带着它任何人都能在浏览器控制台里自己验一遍指纹。
        z.write(src / "verify_web.py", "source/verify_web.py")
        for pkg in ("game", "sim", "web"):
            for f in sorted((src / pkg).rglob("*.py")):
                if "__pycache__" in f.parts:
                    continue
                z.write(f, str(Path("source") / f.relative_to(src)))
        # play.py 在 route_get("/") 里会读它。网页版走不到那条路，
        # 但少一个文件就多一个「为什么这里会炸」，带上更省事。
        z.write(APP, "source/tools/play_app.html")
        for name in DATA_FILES:
            f = ROOT / "data" / name
            if f.exists():
                z.write(f, f"data/{name}")
        for d in DATA_DIRS:
            for f in sorted((ROOT / "data" / d).glob("*.json")):
                z.write(f, f"data/{d}/{f.name}")
        return len(z.namelist())


def _edge_for(rel: str) -> int:
    for prefix, px in MAX_EDGE.items():
        if rel.startswith(prefix):
            return px
    return DEFAULT_EDGE


def build_art(quality: int = 82) -> list[str]:
    """把 manifest 引用到的图 + 视差层转成 WebP，返回可用清单。"""
    from PIL import Image
    sys.path.insert(0, str(ROOT / "source"))
    from game import assets

    art_src = ROOT / "assets" / "art"
    art_out = OUT / "art"
    wanted: set[str] = set(assets.manifest().values())
    # 视差层不在 manifest 里（play.py 是按目录规则直接拼路径的）
    for p in art_src.glob("05_race_parallax/*/final/*.png"):
        wanted.add(str(p.relative_to(art_src)))

    art_out.mkdir(parents=True, exist_ok=True)
    index, total_in, total_out, n = [], 0, 0, 0
    for rel in sorted(wanted):
        src = art_src / rel
        if not src.exists():
            continue
        # 清单里记的是引擎认得的原始路径（.png），发出去的是 .webp——
        # 这个后缀替换必须和 play.py 的 ART_EXT 对齐，否则前端整片 404。
        dst = (art_out / rel).with_suffix(".webp")
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src)
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        edge = _edge_for(rel)
        if max(im.size) > edge:
            im.thumbnail((edge, edge), Image.LANCZOS)
        im.save(dst, "WEBP", quality=quality, method=4)
        total_in += src.stat().st_size
        total_out += dst.stat().st_size
        index.append(rel)
        n += 1
        if n % 40 == 0:
            print(f"    …{n}/{len(wanted)}  {total_out/1048576:.1f} MB", flush=True)
    print(f"  美术 {len(index)} 张：{total_in/1048576:.1f} MB "
          f"→ {total_out/1048576:.1f} MB "
          f"（{total_out/max(1,total_in)*100:.0f}%）")
    return index


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--art", action="store_true", help="同时转美术（慢）")
    ap.add_argument("--local-pyodide", action="store_true",
                    help="引用 play/pyodide/ 下的本地运行时，用于离线自测")
    ap.add_argument("--quality", type=int, default=82)
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    url = LOCAL_PYODIDE if args.local_pyodide else CDN_PYODIDE

    (OUT / "index.html").write_text(build_index(url), encoding="utf-8")
    # 引擎线程。整个 Python 在这里跑，主线程只发消息。
    shutil.copy(ROOT / "source" / "web" / "worker.js", OUT / "worker.js")
    print(f"  index.html  {(OUT/'index.html').stat().st_size/1024:.0f} KB"
          f"  （运行时：{url}）")
    if args.local_pyodide:
        print("  ⚠️  这是离线自测包：它指向 play/pyodide/，而那个目录不进版本库。")
        print("     要发布到 GitHub Pages，请不带 --local-pyodide 重新打包。")

    n = build_zip(OUT / "engine.zip")
    print(f"  engine.zip  {(OUT/'engine.zip').stat().st_size/1024:.0f} KB"
          f"  {n} 个文件")

    idx_file = OUT / "art_index.json"
    if args.art:
        index = build_art(args.quality)
        # 清单里写的是 .png（引擎按逻辑名解析出来的就是 png），
        # 实际发的是 .webp——这一层改写放在这里，引擎不用知道。
        idx_file.write_text(json.dumps(index, ensure_ascii=False),
                            encoding="utf-8")
    elif not idx_file.exists():
        idx_file.write_text("[]", encoding="utf-8")
        print("  art_index.json  空（没带 --art）")

    print(f"\n打包完成 → {OUT}")


if __name__ == "__main__":
    main()
