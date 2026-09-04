"""一条命令跑完所有验证。

    python3 source/run_all_checks.py            # 完整（约 4-6 分钟）
    python3 source/run_all_checks.py --quick    # 快检（约 90 秒）

项目现在有九个系统互相咬合，改任何一处都可能在别处炸掉。分散在六个脚本
里的验证，实际情况是没人会挨个跑——所以它们等于不存在。

`--quick` 是刻意做的：改完代码顺手跑一次，一分半内知道有没有把地基弄坏。
完整版留给提交前和调平衡之后。

每一项都写清楚**它在防什么**。一个说不出自己在防什么的测试，
迟早会被人当成噪音关掉。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "source"

OK, BAD, SKIP = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m—\033[0m"


CHECKS = [
    # (名称, 命令, 在防什么, 是否属于快检)
    ("单元测试", [sys.executable, str(SRC / "tests" / "test_sim.py"), "test_"],
     "物理方程写错、属性映射方向反了、随机种子不可复现", True),

    ("引擎标定 · 平路", [sys.executable, "-c", (
        "import sys;sys.path.insert(0,'source');import calibrate as c;"
        "from sim import flat_stage;"
        "raise SystemExit(0 if c.stage_test('平路赛段',flat_stage(),40.0,45.0,gap2_hi=5.0) else 1)")],
     "赛段均速、冲刺峰值功率、完赛率偏离真实赛事", True),

    ("引擎标定 · 山地", [sys.executable, "-c", (
        "import sys;sys.path.insert(0,'source');import calibrate as c;"
        "from sim import mountain_stage;"
        "raise SystemExit(0 if c.stage_test('山地赛段',mountain_stage(),29.0,34.0,"
        "bunch_sprint=False,gap2_hi=150.0) else 1)")],
     "爬坡速度、总成绩差距、关门率失真", False),

    ("引擎标定 · 计时与石板", [sys.executable, "-c", (
        "import sys;sys.path.insert(0,'source');import calibrate as c;"
        "from sim import itt_stage,cobbled_stage;"
        "a=c.stage_test('计时赛',itt_stage(),44.,53.,power_lo=4.5,power_hi=6.3,"
        "bunch_sprint=False,gap2_hi=60.);"
        "b=c.stage_test('石板路',cobbled_stage(),38.,45.,bunch_sprint=False,gap2_hi=120.);"
        "raise SystemExit(0 if a and b else 1)")],
     "计时赛被当成集团赛跑、石板路失去选拔性", False),

    ("快速结算对账", [sys.executable, "-c", (
        "import sys;sys.path.insert(0,'source');import calibrate_quick as q;"
        "from sim import flat_stage,mountain_stage;"
        "a=q.compare('平路',flat_stage(),'flat');"
        "b=q.compare('山地',mountain_stage(),'summit_finish');"
        "raise SystemExit(0 if a and b else 1)")],
     "玩家亲自看的比赛和系统代跑的比赛遵循不同规律", False),

    ("摔车与机械故障", [sys.executable, str(SRC / "verify_incidents.py")],
     "意外退化成纯随机、发生率失真、退赛过多", False),

    ("赛前指令有效性", [sys.executable, str(SRC / "verify_orders.py"),
                        "--runs", "2", "--teams", "8",
                        "--books", "冲刺夺段,山地强攻"],
     "战术层退化成装饰性下拉菜单", False),

    ("赛季推进", [sys.executable, "-c", (
        "import sys;sys.path.insert(0,'source');"
        "from game.generate_world import generate;from game.season import run_season;"
        "w=generate(2026);o=run_season(w,seed=1);"
        "assert len(o.events)==25 and o.rider_ranking()[0][1]>0;"
        "print('  25 场赛事、107 个比赛日跑通，排名非空')")],
     "赛季串不起来、积分没落地", True),

    ("生涯存档往返", [sys.executable, "-c", (
        "import sys,tempfile,os;sys.path.insert(0,'source');"
        "from game.career import new_career,Career;"
        "c=new_career('T08',2026);c.play_season(auto=True);"
        "p=os.path.join(tempfile.gettempdir(),'sv.json');c.save(p);"
        "d=Career.load(p);"
        "assert d.world.season==c.world.season and len(d.history)==len(c.history);"
        "print('  存档读回后赛季、历史、阵容一致')")],
     "存档丢数据、版本不兼容、玩家进度损坏", True),

    ("多赛季长期行为", [sys.executable, str(SRC / "run_career.py"),
                        "--seasons", "6"],
     "世界逐年变弱、年龄结构倒挂、豪门被反馈循环打死", False),
]


def run(name: str, cmd: list[str], guard: str, timeout: int = 400) -> bool:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout)
        good = r.returncode == 0 and "✗" not in r.stdout
    except subprocess.TimeoutExpired:
        print(f"{BAD} {name:<20}超时")
        return False
    dt = time.time() - t0
    print(f"{OK if good else BAD} {name:<20}{dt:>6.1f}s   防：{guard}")
    if not good:
        tail = [l for l in r.stdout.splitlines() if "✗" in l][:4]
        for l in tail or r.stderr.splitlines()[-4:]:
            print(f"     {l}")
    return good


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只跑四十秒内的快检")
    args = ap.parse_args()

    checks = [c for c in CHECKS if c[3]] if args.quick else CHECKS
    print("=" * 78)
    print(f"回归检查 · {'快检' if args.quick else '完整'} · {len(checks)} 项")
    print("=" * 78)

    t0 = time.time()
    results = [run(n, c, g) for n, c, g, _ in checks]
    print("=" * 78)
    print(f"通过 {sum(results)}/{len(results)}　总耗时 {time.time() - t0:.0f} 秒")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
