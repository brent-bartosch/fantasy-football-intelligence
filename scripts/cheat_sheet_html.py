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

from ffi.breakout import attach, load_notes
from ffi.db import connect
from ffi.sim.pool import build_pool

# Depth resized 2026-08-29 (draft day) from OUR league's only clean 12-team /
# 228-pick draft (2025; earlier seasons in draft_picks mix two 14-16 team
# leagues and are not comparable). Positions actually drafted in 2025:
#   RB 69 · WR 72 · QB 46 · TE 20 · DEF 12 · K 9
# Depth = that demand plus buffer, truncated where the projection curve dies:
#   RB  proj 88.9 @69 -> 50.1 @85 -> 20.6 @100   (meaningful through ~85)
#   WR  proj 126  @72 -> 74.3 @110               (full-PPR WR stays flat+deep)
#   QB  proj 258  @30 -> 163 @32 -> 56 @36       (hard cliff at 32: past it the
#       names are backups with ~0 expected starts, so 2025's 46 drafted QBs are
#       lottery tickets our board cannot price -- shown, but not projectable)
DEPTH = {"QB": 36, "RB": 85, "WR": 110, "TE": 36, "DEF": 20, "K": 20}

# "All viable" flat list: union of (a) anyone the market prices as draftable
# (real Sleeper adp_2qb, i.e. not the 999 sentinel) and (b) anyone our board
# projects at or above the proj of the last player drafted at that position in
# 2025. Neither source alone is enough -- (a) misses players the market has not
# priced yet, (b) misses players the market drafts on role speculation that our
# projection zeroes. Union is ~421 names for a 240-pick draft plus waiver churn.
DEMAND_2025 = {"RB": 69, "WR": 72, "QB": 46, "TE": 20, "DEF": 12, "K": 9}
ORDER = ["RB", "WR", "QB", "TE", "DEF", "K"]  # draft-priority order, left->right

PLAYBOOK = (
    "RB scarce (steep cliff) — draft early. · WR deep & flat — wait, get volume. · "
    "QB deep — get 2 startable, don't overpay; QB3 R10+. · TE: 1 starter + 1 backup. · "
    "K/DEF last two rounds. · Depth priority in-season: RB & WR."
)


def viable_floors(pool):
    """proj_points of the last player drafted at each position in 2025 -- the
    empirical "still worth a roster spot" line. Fails loud if a position is
    missing from the pool rather than silently omitting it from the flat list."""
    floors = {}
    for pos, dem in DEMAND_2025.items():
        ps = sorted(
            (p for p in pool if p.position == pos), key=lambda p: -p.proj_points
        )
        if not ps:
            raise ValueError(
                f"no {pos} in pool -- cannot compute viability floor; "
                f"upstream valuation/pool invariant broken"
            )
        floors[pos] = float(ps[min(dem, len(ps)) - 1].proj_points)
    return floors


def build_viable(pool, notes_by_ref=None):
    """Flat cross-position list of every viable player, ranked by VORP.

    VORP is already replacement-normalized per position, so it is the only
    number in the pool that is meaningfully comparable across QB/RB/WR/TE --
    proj_points is not (a 250-point QB and a 250-point RB are worlds apart).
    This is the list to read in rounds 12+ when the position columns run dry.
    """
    notes_by_ref = notes_by_ref or {}
    floors = viable_floors(pool)
    # The flat list must be a strict SUPERSET of the position columns: a player
    # good enough to display in a column but absent from "all viable" makes the
    # list a liar. The columns run deeper than the 2025 floor at RB/WR/TE (they
    # rank by proj, not by the drafted line), so fold them in explicitly.
    shown = set()
    for pos, depth in DEPTH.items():
        ranked = sorted(
            (q for q in pool if q.position == pos), key=lambda q: -q.proj_points
        )
        shown.update(q.ref for q in ranked[:depth])
    rows = []
    for p in pool:
        floor = floors.get(p.position)
        if floor is None:
            continue
        priced = p.adp is not None
        if not priced and float(p.proj_points) < floor and p.ref not in shown:
            continue
        row = {
            "id": p.ref,
            "n": p.name,
            "pos": p.position,
            "proj": round(p.proj_points),
            "vorp": round(p.vorp),
            "t": p.tier,
            "adp": round(p.adp) if p.adp is not None else None,
            # market prices him but our projection is below the 2025 drafted
            # line -- i.e. the market is buying a role our board does not see
            "spec": bool(priced and float(p.proj_points) < floor),
        }
        note = notes_by_ref.get(p.ref)
        if note is not None:
            row["bo"] = {
                "c": note.category,
                "b": note.badge,
                "th": note.thesis,
                "k": note.kill,
            }
        rows.append(row)
    # K/DEF VORP is on a compressed scale (max ~11/~19 vs RB's ~288) because
    # every team starts exactly one and the spread between them is tiny. Sorted
    # naively they land mid-list, ABOVE genuinely useful deep RB/WR whose VORP
    # is very negative -- which inverts the playbook (K/DEF go in the last two
    # rounds, always). Keep them on the list, pinned below every skill player.
    rows.sort(key=lambda r: (r["pos"] in ("K", "DEF"), -r["vorp"]))
    return rows


def build(pool, notes_by_ref=None):
    """Column data for the page. `notes_by_ref` is the curated breakout layer
    (annotations only -- it never changes proj/vorp/tier/ordering)."""
    notes_by_ref = notes_by_ref or {}
    cols = {}
    for pos in ORDER:
        ps = sorted(
            (p for p in pool if p.position == pos), key=lambda p: -p.proj_points
        )
        rows = []
        for p in ps[: DEPTH[pos]]:
            row = {
                "id": p.ref,
                "n": p.name,
                "proj": round(p.proj_points),
                "vorp": round(p.vorp),
                "t": p.tier,
                "adp": round(p.adp) if p.adp is not None else None,
            }
            note = notes_by_ref.get(p.ref)
            if note is not None:
                row["bo"] = {
                    "c": note.category,
                    "b": note.badge,
                    "th": note.thesis,
                    "k": note.kill,
                }
            rows.append(row)
        cols[pos] = rows
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
.row{{display:flex;align-items:center;gap:4px;padding:3px 6px;border-left:3px solid var(--t6);cursor:pointer}}
.row:hover{{background:#222b36}}
.row.d{{opacity:.32;text-decoration:line-through}}
.rk{{color:var(--dim);width:17px;text-align:right;font-variant-numeric:tabular-nums}}
.nm{{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.num{{color:var(--dim);font-size:11px;font-variant-numeric:tabular-nums}}
.adp{{width:34px;text-align:right}}
.t1{{border-left-color:var(--t1)}}.t2{{border-left-color:var(--t2)}}.t3{{border-left-color:var(--t3)}}
.t4{{border-left-color:var(--t4)}}.t5{{border-left-color:var(--t5)}}.t6{{border-left-color:var(--t6)}}
.hi{{background:#243b53}}
/* breakout notes: annotations only — badges never change ordering or numbers */
.bo{{flex:none;width:12px;height:12px;line-height:12px;border-radius:2px;font-size:9px;
font-weight:700;text-align:center;color:#0b0f14;cursor:pointer}}
.bo-situation{{background:#d29922}}.bo-post-injury{{background:#f85149}}
.bo-year-n-leap{{background:#3fb950}}.bo-role-path{{background:#58a6ff}}
.note{{padding:5px 8px 7px 26px;background:#141b24;border-left:3px solid #30363d;font-size:11px;color:#c9d1d9}}
.note b{{color:#8b98a5;font-weight:600}}
.note .kill{{color:#d29922;display:block;margin-top:3px}}
button.on{{background:#243b53;border-color:#58a6ff}}
/* flat "all viable" view: one wide ranked column, VORP-sorted */
.col.wide{{min-width:100%}}
.pos{{flex:none;width:26px;font-size:10px;font-weight:700;color:var(--dim)}}
.vorp{{width:42px;text-align:right}}
.spec{{color:#d29922}}   /* market prices him, our projection does not */
.legend{{color:var(--dim);font-size:10px;margin-top:4px}}
.legend span{{margin-right:8px}}
.legend i{{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:3px}}
</style></head><body>
<header>
<h1>Draft Cheat Sheet — {date} <span class=cnt>· our scoring · incompletion-fixed</span></h1>
<div class=play>{play}</div>
<div class=bar>
<input id=q placeholder="type a drafted player → Enter to cross off (fuzzy)">
<button onclick=reset()>Reset</button>
<button id=bof onclick=togBo()>★ breakouts</button>
<button id=allf onclick=togAll()>▤ all viable</button>
<span class=cnt id=cnt></span>
</div>
<div class=legend>
<span><i style="background:#d29922"></i>S situation</span>
<span><i style="background:#f85149"></i>I post-injury</span>
<span><i style="background:#3fb950"></i>Y year-2/3 leap</span>
<span><i style="background:#58a6ff"></i>R role path</span>
<span>· click a badge for the thesis + what kills it</span>
</div></header>
<div class=cols id=cols></div>
<script>
const DATA={data}, ORDER={order}, VIABLE={viable};
const drafted=new Set(JSON.parse(localStorage.getItem('drafted_{date}')||'[]'));
const openNotes=new Set();      // which theses are expanded (view state, not persisted)
let boOnly=false;               // ★ breakouts filter
let allMode=false;              // ▤ flat all-viable view (VORP-ranked)
function save(){{localStorage.setItem('drafted_{date}',JSON.stringify([...drafted]));upd();}}
function reset(){{drafted.clear();save();render();}}
function upd(){{document.getElementById('cnt').textContent=drafted.size+' off the board';}}
function toggle(id){{drafted.has(id)?drafted.delete(id):drafted.add(id);save();render();}}
function esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
function togNote(ev,id){{
 ev.stopPropagation();           // never cross a player off just by reading their note
 openNotes.has(id)?openNotes.delete(id):openNotes.add(id);render();
}}
function togBo(){{
 boOnly=!boOnly;
 document.getElementById('bof').classList.toggle('on',boOnly);
 render();
}}
function togAll(){{
 allMode=!allMode;
 document.getElementById('allf').classList.toggle('on',allMode);
 render();
}}
// Flat view shares the SAME drafted set as the columns, so crossing a player
// off in either view removes him from both -- there is only one board.
function renderAll(){{
 const c=document.getElementById('cols');c.innerHTML='';
 const col=document.createElement('div');col.className='col wide';
 let h='<h2>ALL VIABLE — '+VIABLE.length+' names, ranked by VORP (cross-position)</h2><div class=list>';
 let rk=0;
 for(const p of VIABLE){{
  if(!drafted.has(p.id))rk++;
  if(boOnly&&!p.bo)continue;
  const d=drafted.has(p.id)?' d':'';
  const adp=p.adp==null?'—':p.adp;
  const badge=p.bo?'<span class="bo bo-'+p.bo.c+'" title="'+esc(p.bo.c)+
    '" onclick="togNote(event,\''+p.id+'\')">'+p.bo.b+'</span>':'';
  h+='<div class="row t'+p.t+d+'" data-n="'+p.n.toLowerCase()+'" onclick="toggle(\''+p.id+'\')">'+
     '<span class=rk>'+(drafted.has(p.id)?'·':rk)+'</span>'+
     '<span class=pos>'+p.pos+'</span>'+
     badge+
     '<span class="nm'+(p.spec?' spec':'')+'">'+p.n+'</span>'+
     '<span class="num">'+p.proj+'</span>'+
     '<span class="num vorp">'+p.vorp+'</span>'+
     '<span class="num adp">'+adp+'</span></div>';
  if(p.bo&&openNotes.has(p.id))
    h+='<div class=note>'+esc(p.bo.th)+
       '<span class=kill><b>kills it:</b> '+esc(p.bo.k)+'</span></div>';
 }}
 col.innerHTML=h+'</div>';c.appendChild(col);
 upd();
}}
function render(){{
 if(allMode)return renderAll();
 const c=document.getElementById('cols');c.innerHTML='';
 for(const pos of ORDER){{
  const col=document.createElement('div');col.className='col';
  let h='<h2>'+pos+'</h2><div class=list>';
  let rk=0;
  for(const p of DATA[pos]){{
   if(!drafted.has(p.id))rk++;      // rank counts the true board, not the filtered view
   if(boOnly&&!p.bo)continue;
   const d=drafted.has(p.id)?' d':'';
   const adp=p.adp==null?'—':p.adp;
   const badge=p.bo?'<span class="bo bo-'+p.bo.c+'" title="'+esc(p.bo.c)+
     '" onclick="togNote(event,\\''+p.id+'\\')">'+p.bo.b+'</span>':'';
   h+='<div class="row t'+p.t+d+'" data-n="'+p.n.toLowerCase()+'" onclick="toggle(\\''+p.id+'\\')">'+
      '<span class=rk>'+(drafted.has(p.id)?'·':rk)+'</span>'+
      badge+
      '<span class=nm>'+p.n+'</span>'+
      '<span class="num">'+p.proj+'</span>'+
      '<span class="num adp">'+adp+'</span></div>';
   if(p.bo&&openNotes.has(p.id))
     h+='<div class=note>'+esc(p.bo.th)+
        '<span class=kill><b>kills it:</b> '+esc(p.bo.k)+'</span></div>';
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
 const hunt=allMode?[VIABLE]:ORDER.map(pos=>DATA[pos]);
 for(const list of hunt)for(const p of list)
   if(!drafted.has(p.id)&&p.n.toLowerCase().includes(v)){{drafted.add(p.id);save();render();q.value='';return;}}
}});
</script></body></html>"""


def main():
    conn = connect()
    pool = build_pool(conn, "qb_hoard_12")
    # Both calls fail loud (BreakoutNotesError) rather than dropping a note:
    # a curated thesis that silently never renders is worse than no layer at all.
    notes = attach(load_notes(), pool, DEPTH)
    date = datetime.date.today().isoformat()
    page = PAGE.format(
        date=date,
        play=html.escape(PLAYBOOK),
        data=json.dumps(build(pool, notes)),
        order=json.dumps(ORDER),
        viable=json.dumps(build_viable(pool, notes)),
    )
    path = "reports/cheat-sheet.html"
    with open(path, "w") as f:
        f.write(page)
    print(
        f"wrote {path} — open in a browser; click or search to cross players off.\n"
        f"  breakout notes: {len(notes)} placed"
    )


if __name__ == "__main__":
    main()
