"""把 20 个事件的插画简报导出成一份可以直接给 GPT 用的文件。

    python3 source/tools/export_art_briefs.py

事件插画是美术清单里"最值得投入"的一项——没有插画的事件弹窗极其单薄。
但要 GPT 画得对，得先告诉它画什么。这份文件把每张图的内容、情绪、构图
和统一风格锁一次性写清楚，复制粘贴就能开工。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.events import EVENTS  # noqa: E402

STYLE_LOCK = """```
Chibi 3-head-tall (三头身) 2D game art, professional road cycling theme.
Style: clean flat cel-shading, 3px dark outline (outline = base color darkened 40%),
single hard shadow layer (no soft gradients), saturated but not neon palette,
mobile game UI quality, readable at 1200x675.
Rendering: front-lit, no ambient occlusion, no photorealism, no 3D render look.
Composition: cinematic 16:9, single clear focal point, background simplified.
```

**统一负向词**
```
no text, no letters, no watermark, no signature, no realistic proportions,
no anime teen style, no 3D render, no gradient mesh, no drop shadow blur,
no gore, no blood, no brand logos
```
"""


def main() -> None:
    lines: list[str] = [
        "# 事件插画出图简报（20 张）",
        "",
        "**目标规格**：1200×675，不透明底，输出到 `assets/art/09_misc/events/`",
        "",
        "**命名**：严格按下表的文件名。游戏数据里已经按这个名字绑好引用，"
        "改名会导致弹窗里出现空白。",
        "",
        "---",
        "",
        "## 风格锁（每次出图都粘这一段）",
        "",
        STYLE_LOCK,
        "---",
        "",
        "## 通用要求",
        "",
        "这 20 张是玩家在经营界面里唯一会**停下来看**的图，权重高于所有 UI 图标。"
        "三条硬要求：",
        "",
        "一、**情绪必须一眼可读**。玩家不会读完文案再看图，图要先把气氛立住——"
        "是压抑、是紧张、还是庆祝。",
        "",
        "二、**不要画脸部特写**。三头身的脸承载不了细腻表情，靠姿态、光线和构图讲事情。",
        "",
        "三、**背景一律简化**。这些图会被弹窗的半透明遮罩压住一部分，"
        "细节太多会糊成一团。",
        "",
        "---",
        "",
    ]

    for e in EVENTS:
        lines += [
            f"### {e.art}.png　—　{e.title}",
            "",
            f"**分类**：{e.category.value}　**触发时机**：{e.when.value}",
            "",
            f"**画面**：{e.art_brief}",
            "",
            f"**这张图要传达的情绪**：{_mood(e)}",
            "",
            "---",
            "",
        ]

    lines += [
        "## 出图顺序建议",
        "",
        "先出 `event-20`（夺冠）和 `event-01`（摔车）这两张——它们是情绪光谱的"
        "两个极端。这两张的风格定下来，中间十八张就有了参照，不会画着画着跑偏。",
        "",
        "`event-19`（深夜账单）和 `event-09`（老将告别）是全套里最难的两张，"
        "放到最后画，那时风格已经稳了。",
    ]

    out = ROOT / "事件插画_出图简报.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"简报已生成 -> {out.name}（{len(EVENTS)} 张）")

    from collections import Counter
    cats = Counter(e.category.value for e in EVENTS)
    print("  分类分布：" + "、".join(f"{k} {v}" for k, v in cats.most_common()))
    n_choices = sum(len(e.choices) for e in EVENTS)
    n_risk = sum(1 for e in EVENTS for c in e.choices if c.risk)
    print(f"  共 {n_choices} 个选项，其中 {n_risk} 个带风险判定")


def _mood(e) -> str:
    return {
        "伤病": "受挫与不确定，冷色，不要血腥",
        "士气": "人与人之间的张力，靠站位和视线讲",
        "赞助": "商业感，克制的对立，暖色但不亲切",
        "转会": "被拉扯的犹豫感",
        "媒体": "被围观的压力",
        "青训": "安静的希望",
        "器材": "专业、冷静、技术质感",
        "操守": "克制的沉重，绝不猎奇",
    }[e.category.value]


if __name__ == "__main__":
    main()
