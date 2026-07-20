#!/usr/bin/env python3
"""Interactive HTML draft cheat sheet under OUR league scoring.

Position columns side-by-side (QB/RB/WR/TE/K/DEF), tier color-coding, deep RB/WR
lists, and click-or-search to cross players off live during a draft (best
available floats up; state persists in localStorage). Built for cross-reference
during an FP mock -- far more scannable than the markdown version.

Usage: uv run python scripts/cheat_sheet_html.py   ->  reports/cheat-sheet.html
"""
import datetime
import html
import json

from ffi.db import connect
from ffi.sim.pool import build_pool

DEPTH = {"QB": 30, "RB": 55, "WR": 60, "TE": 26, "DEF": 16, "K": 15}
ORDER = ["RB", "WR", "QB", "TE", "DEF", "K"]  # draft-priority order, left->right

PLAYBOOK = (
    "RB scarce (steep cliff) — draft early. · WR deep & flat — wait, get volume. · "
    "QB deep — get 2 startable, don't overpay; QB3 R10+. · TE: 1 starter + 1 backup. · "
    "K/DEF last two rounds. · Depth priority in-season: RB & WR."
)


def build(pool):
    cols = {}
    for pos in ORDER:
        ps = sorted(
            (p for p in pool if p.position == pos), key=lambda p: -p.proj_points
        )
        cols[pos] = [
            {
                "id": p.ref,
                "n": p.name,
                "proj": round(p.proj_points),
                "vorp": round(p.vorp),
                "t": p.tier,
                "adp": round(p.adp) if p.adp is not None else None,
            }
            for p in ps[: DEPTH[pos]]
        ]
    return cols


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Draft Cheat Sheet — {date}</title>
<style>
:root{{--bg:#0f1419;--card:#1a2029;--ink:#e6edf3;--dim:#8b98a5;
--t1:#2ea043;--t2:#1f6feb;--t3:#8957e5;--t4:#9e6a03;--t5:#6e7681;--t6:#484f58;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:13px/1.3 -apple-system,Segoe UI,Roboto,sans-serif}}
header{{position:sticky;top:0;z-index:5;background:#0b0f14;padding:8px 12px;border-bottom:1px solid #222}}
h1{{margin:0 0 4px;font-size:15px}}
.play{{color:var(--dim);font-size:11px;margin-bottom:6px}}
.bar{{display:flex;gap:8px;align-items:center}}
input{{flex:1;max-width:340px;background:var(--card);border:1px solid #30363d;color:var(--ink);
padding:6px 10px;border-radius:6px;font-size:13px}}
button{{background:var(--card);border:1px solid #30363d;color:var(--ink);padding:6px 10px;border-radius:6px;cursor:pointer}}
.cnt{{color:var(--dim);font-size:11px}}
.cols{{display:flex;gap:8px;padding:8px;overflow-x:auto;align-items:flex-start}}
.col{{background:var(--card);border-radius:8px;min-width:210px;flex:1;overflow:hidden}}
.col h2{{margin:0;font-size:12px;padding:6px 8px;background:#11161d;position:sticky;top:0}}
.col .list{{max-height:82vh;overflow:auto}}
.row{{display:flex;align-items:center;gap:6px;padding:3px 8px;border-left:3px solid var(--t6);cursor:pointer}}
.row:hover{{background:#222b36}}
.row.d{{opacity:.32;text-decoration:line-through}}
.rk{{color:var(--dim);width:20px;text-align:right;font-variant-numeric:tabular-nums}}
.nm{{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.num{{color:var(--dim);font-size:11px;font-variant-numeric:tabular-nums}}
.adp{{width:34px;text-align:right}}
.t1{{border-left-color:var(--t1)}}.t2{{border-left-color:var(--t2)}}.t3{{border-left-color:var(--t3)}}
.t4{{border-left-color:var(--t4)}}.t5{{border-left-color:var(--t5)}}.t6{{border-left-color:var(--t6)}}
.hi{{background:#243b53}}
</style></head><body>
<header>
<h1>Draft Cheat Sheet — {date} <span class=cnt>· our scoring · incompletion-fixed</span></h1>
<div class=play>{play}</div>
<div class=bar>
<input id=q placeholder="type a drafted player → Enter to cross off (fuzzy)">
<button onclick=reset()>Reset</button>
<span class=cnt id=cnt></span>
</div></header>
<div class=cols id=cols></div>
<script>
const DATA={data}, ORDER={order};
const drafted=new Set(JSON.parse(localStorage.getItem('drafted_{date}')||'[]'));
function save(){{localStorage.setItem('drafted_{date}',JSON.stringify([...drafted]));upd();}}
function reset(){{drafted.clear();save();render();}}
function upd(){{document.getElementById('cnt').textContent=drafted.size+' off the board';}}
function toggle(id){{drafted.has(id)?drafted.delete(id):drafted.add(id);save();render();}}
function render(){{
 const c=document.getElementById('cols');c.innerHTML='';
 for(const pos of ORDER){{
  const col=document.createElement('div');col.className='col';
  let h='<h2>'+pos+'</h2><div class=list>';
  let rk=0;
  for(const p of DATA[pos]){{
   if(!drafted.has(p.id))rk++;
   const d=drafted.has(p.id)?' d':'';
   const adp=p.adp==null?'—':p.adp;
   h+='<div class="row t'+p.t+d+'" data-n="'+p.n.toLowerCase()+'" onclick="toggle(\\''+p.id+'\\')">'+
      '<span class=rk>'+(drafted.has(p.id)?'·':rk)+'</span>'+
      '<span class=nm>'+p.n+'</span>'+
      '<span class="num">'+p.proj+'</span>'+
      '<span class="num adp">'+adp+'</span></div>';
  }}
  col.innerHTML=h+'</div>';c.appendChild(col);
 }}
 upd();
}}
render();
// search: fuzzy match by last name, Enter crosses off the top hit
const q=document.getElementById('q');
q.addEventListener('input',()=>{{
 const v=q.value.trim().toLowerCase();
 document.querySelectorAll('.row').forEach(r=>r.classList.toggle('hi',v&&r.dataset.n.includes(v)));
}});
q.addEventListener('keydown',e=>{{
 if(e.key!=='Enter')return;
 const v=q.value.trim().toLowerCase();if(!v)return;
 for(const pos of ORDER)for(const p of DATA[pos])
   if(!drafted.has(p.id)&&p.n.toLowerCase().includes(v)){{drafted.add(p.id);save();render();q.value='';return;}}
}});
</script></body></html>"""


def main():
    conn = connect()
    pool = build_pool(conn, "qb_hoard_12")
    date = datetime.date.today().isoformat()
    page = PAGE.format(
        date=date,
        play=html.escape(PLAYBOOK),
        data=json.dumps(build(pool)),
        order=json.dumps(ORDER),
    )
    path = "reports/cheat-sheet.html"
    with open(path, "w") as f:
        f.write(page)
    print(f"wrote {path} — open in a browser; click or search to cross players off.")


if __name__ == "__main__":
    main()
