"""把回放数据打进 HTML 模板，产出一个双击就能打开的原型文件。

    python3 source/tools/export_replay.py --stage mountain
    python3 source/tools/build_prototype.py

之所以要把 JSON 内联进 HTML，是因为浏览器不允许 file:// 页面 fetch 本地
文件。内联之后不需要起服务器，直接双击就能看——这在给非技术同事演示时
是刚需，"先装个 Python 再开个 http.server"会劝退所有人。

图片仍然走相对路径读 assets/art，所以这个 HTML 必须放在项目根目录。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).resolve().parent / "prototype_template.html"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="data/replay.json")
    ap.add_argument("--out", default="原型_比赛回放.html")
    args = ap.parse_args()

    replay_path = ROOT / args.replay
    if not replay_path.exists():
        raise SystemExit(f"找不到回放数据 {replay_path}，先跑 export_replay.py")

    data = replay_path.read_text(encoding="utf-8")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("/*__REPLAY__*/null", data)

    out = ROOT / args.out
    out.write_text(html, encoding="utf-8")

    payload = json.loads(data)
    print(f"原型已生成 -> {out.name}  ({out.stat().st_size / 1024:.0f} KB)")
    print(f"  赛段 {payload['stage']['name']}  "
          f"{payload['stage']['length_m'] / 1000:.1f} km  "
          f"爬升 {payload['stage']['ascent_m']} m")
    print(f"  {len(payload['frames'])} 帧回放，"
          f"{len(payload['riders'])} 名车手的队服配色与头像绑定")
    print("  双击打开即可，图片按相对路径读 assets/art/")


if __name__ == "__main__":
    main()
