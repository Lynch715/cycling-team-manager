"""把成绩库导出成一个可以直接打开的历史档案页面。

用法：
    python3 source/tools/export_history.py --sim 25

生成 `历史档案.html`——单文件，数据内联，双击就能看，不需要服务器。
这一页同时服务两种读者：上半页是玩家要看的名人堂和赛道纪录，
下半页是设计者要看的平衡诊断。放在一起是故意的：**判断「这个世界是否
可信」和判断「这套数值是否平衡」用的其实是同一批数字。**
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source"))

from game.records import (age_of_winners, career_wins, connect,  # noqa: E402
                          course_records, dynasty_check, nation_medals,
                          role_balance, summary, wins_by_stage_type,
                          world_level)

OUT = ROOT / "历史档案.html"

ROLE_CN = {
    "sprinter": "冲刺手", "climber": "爬坡手", "rouleur": "全能型",
    "leader": "总成绩核心", "domestique": "工兵", "leadout": "冲刺列车",
    "breakaway": "突围手",
}
TYPE_CN = {
    "flat": "平路", "hilly": "丘陵", "mountain": "山地",
    "summit_finish": "山顶终点", "cobbled": "石板路", "itt": "个人计时",
    "gc": "总成绩",
}


def collect(conn) -> dict:
    return {
        "summary": summary(conn),
        "hall": career_wins(conn, 20),
        "records": course_records(conn, 40),
        "dynasty": dynasty_check(conn)[:10],
        "nations": nation_medals(conn, 12),
        "roles": role_balance(conn),
        "terrain": wins_by_stage_type(conn),
        "ages": age_of_winners(conn),
        "level": world_level(conn),
        "role_cn": ROLE_CN,
        "type_cn": TYPE_CN,
    }


TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>历史档案</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b35;--fg:#e6e8ec;--dim:#8b93a3;
      --hi:#ffcf5c;--ok:#5ad19a;--bad:#ff6b6b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:36px 20px 80px}
h1{font-size:30px;margin:0 0 4px;letter-spacing:.5px}
.sub{color:var(--dim);margin-bottom:30px}
h2{font-size:19px;margin:38px 0 6px;padding-bottom:8px;
   border-bottom:1px solid var(--line)}
.note{color:var(--dim);font-size:13.5px;margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:16px 18px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{color:var(--dim);font-weight:500;text-align:left;padding:6px 8px;
   border-bottom:1px solid var(--line);font-size:12.5px}
td{padding:6px 8px;border-bottom:1px solid #1e222b}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.rank{color:var(--dim);width:30px}
.gold{color:var(--hi);font-weight:600}
.bar{height:9px;border-radius:5px;background:#2a3140;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,#4a90d9,#7bc0ff)}
.stack{display:flex;height:22px;border-radius:5px;overflow:hidden;
       font-size:11px;line-height:22px;color:#0c0e12}
.stack span{text-align:center;white-space:nowrap;overflow:hidden}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.spark{display:flex;align-items:flex-end;gap:3px;height:70px}
.spark div{flex:1;background:#3b6ea5;border-radius:2px 2px 0 0}
.flag{color:var(--dim);font-size:12.5px}
.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11.5px;
     background:#232936;color:var(--dim);margin-left:6px}
.warn{color:var(--bad)}.good{color:var(--ok)}
</style></head><body><div class="wrap">
<h1>历史档案</h1>
<div class="sub" id="sub"></div>
<div id="app"></div>
</div>
<script>
const D = __DATA__;
const rc = r => D.role_cn[r] || r, tc = t => D.type_cn[t] || t;
const hms = s => s==null ? "—" :
  `${Math.floor(s/3600)}:${String(Math.floor(s%3600/60)).padStart(2,"0")}:${String(Math.floor(s%60)).padStart(2,"0")}`;
const PAL = ["#4a90d9","#e8a33d","#5ad19a","#c678dd","#ff6b6b","#7bc0ff","#8b93a3"];

const S = D.summary;
sub.textContent = `${S.seasons} 个赛季 · ${S.rows} 条成绩 · ${S.riders} 名车手留下过名字`;

function tbl(head, rows){
  return `<table><tr>${head.map(h=>`<th class="${h[1]||''}">${h[0]}</th>`).join("")}</tr>`
    + rows.map(r=>`<tr>${r.join("")}</tr>`).join("") + `</table>`;
}
const td = (v,c="") => `<td class="${c}">${v}</td>`;

let h = "";

/* ---- 名人堂 ---- */
h += `<h2>名人堂</h2>
<p class="note">按大赛（大环赛与纪念碑）冠军数排序。总冠军数包含赛段胜利。</p>
<div class="card">` + tbl(
  [["#"],["车手"],["大赛冠军","n"],["总冠军","n"]],
  D.hall.map((r,i)=>[td(i+1,"rank"),
    td(`<span class="${i<3?'gold':''}">${r.rider_name}</span>`),
    td(r.big,"n"), td(r.wins,"n")])) + `</div>`;

/* ---- 赛道纪录 ---- */
h += `<h2>赛道纪录</h2>
<p class="note">每条赛道跑出过的最快成绩。纪录长期不被打破，说明那个年代的世界水平偏高。</p>
<div class="card">` + tbl(
  [["赛事"],["赛段","n"],["纪录","n"],["保持者"],["赛季","n"]],
  D.records.slice(0,25).map(r=>{
    const st = (r.course_id||"").split("-").pop();
    return [td(r.race_name), td(st,"n"), td(hms(r.best),"n"),
            td(r.rider_name), td(r.season,"n")];
  })) + `</div>`;

/* ---- 王朝 + 国籍 ---- */
h += `<h2>车队与国籍</h2><div class="grid">
<div class="card"><p class="note">拿过年度积分第一的车队</p>` + tbl(
  [["车队"],["年度第一","n"]],
  D.dynasty.map(r=>[td(r.team_name), td(r.titles,"n")])) + `</div>
<div class="card"><p class="note">各国车手胜场</p>` + tbl(
  [["国籍"],["胜场","n"]],
  D.nations.map(r=>[td(r.nation), td(r.wins,"n")])) + `</div></div>`;

/* ---- 角色平衡 ---- */
const maxP = Math.max(...D.roles.map(r=>r.per100||0));
h += `<h2>角色产出 · 平衡诊断</h2>
<p class="note"><b>per100 = 每一百个「车手赛季」赢几场。</b>
原始胜场数没有意义——工兵占了半个车队，胜场当然多。
除以人数之后，这张表才回答了玩家真正在问的问题：签哪种车手划算。</p>
<div class="card">` + tbl(
  [["角色"],["人数","n"],["胜场","n"],["per100","n"],["产出强度"],
   ["总成绩","n"],["大环赛","n"],["纪念碑","n"]],
  D.roles.map(r=>[td(rc(r.role)), td(r.pop,"n"), td(r.wins,"n"),
    td(`<b>${r.per100}</b>`,"n"),
    td(`<div class="bar"><i style="width:${(r.per100/maxP*100).toFixed(0)}%"></i></div>`),
    td(r.overalls,"n"), td(r.gt,"n"), td(r.mon,"n")])) + `</div>`;

/* ---- 地形筛选 ---- */
h += `<h2>地形筛选</h2>
<p class="note">每种赛段的冠军由谁拿走。<b>这是最硬的一张表</b>——
如果山地赛段的冠军里冲刺手占了两成，说明地形根本没在筛人，赛道设计对结果不起作用。</p>
<div class="card">`;
for(const t of D.terrain){
  const es = Object.entries(t.shares).filter(([,v])=>v>=2);
  h += `<div style="margin:12px 0">
    <div style="display:flex;justify-content:space-between;margin-bottom:5px">
      <b>${tc(t.stage_type)}</b><span class="flag">${t.total} 场</span></div>
    <div class="stack">` +
    es.map(([k,v],i)=>`<span style="width:${v}%;background:${PAL[i%PAL.length]}"
      title="${rc(k)} ${v}%">${v>=9?rc(k):""}</span>`).join("") +
    `</div><div class="flag" style="margin-top:4px">` +
    es.slice(0,5).map(([k,v])=>`${rc(k)} ${v}%`).join("　") + `</div></div>`;
}
h += `</div>`;

/* ---- 夺冠年龄 + 世界水平 ---- */
const maxA = Math.max(...D.ages.map(a=>a.wins));
h += `<h2>夺冠年龄与世界水平</h2><div class="grid">
<div class="card"><p class="note">真实公路车的夺冠年龄集中在 26–31 岁</p>` + tbl(
  [["年龄段"],["胜场","n"],["分布"]],
  D.ages.map(a=>[td(a.band), td(a.wins,"n"),
    td(`<div class="bar"><i style="width:${(a.wins/maxA*100).toFixed(0)}%"></i></div>`)]))
  + `</div><div class="card"><p class="note" id="lvnote"></p>
  <div class="spark" id="spark"></div>
  <div class="flag" id="lvax" style="display:flex;justify-content:space-between;margin-top:5px"></div>
  </div></div>`;

app.innerHTML = h;

const L = D.level, vs = L.map(r=>r.level);
const lo = Math.min(...vs), hi = Math.max(...vs), swing = (hi-lo).toFixed(1);
spark.innerHTML = L.map(r=>{
  const pct = 18 + (r.level-lo)/Math.max(.1,hi-lo)*82;
  return `<div style="height:${pct}%" title="${r.season}: ${r.level}"></div>`;
}).join("");
lvax.innerHTML = `<span>${L[0]?.season??""}</span><span>${L.at(-1)?.season??""}</span>`;
lvnote.innerHTML = `每季前 20 名车手的平均总评，应当平稳。波动 ${lo} – ${hi}
  <span class="tag ${swing>3?'warn':'good'}">${swing} 分${swing>3?' · 稳态控制在震荡':' · 正常'}</span>`;
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--sim", type=int, default=0)
    args = ap.parse_args()

    path = Path(args.db) if args.db else None
    if args.sim:
        from history import simulate
        path = path or ROOT / "data" / "records.db"
        print(f"空跑 {args.sim} 个赛季……")
        conn = simulate(args.sim, path)
    else:
        conn = connect(path)

    data = collect(conn)
    if data["summary"]["rows"] == 0:
        print("成绩库是空的。加 --sim 25 先跑几个赛季。")
        return
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    OUT.write_text(html, encoding="utf-8")
    print(f"→ {OUT.name}  ({len(html) // 1024} KB, "
          f"{data['summary']['seasons']} 个赛季)")


if __name__ == "__main__":
    main()
