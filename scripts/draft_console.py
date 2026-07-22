#!/usr/bin/env python3
"""Live draft console (design 2026-07-22): a single self-contained HTML file
that runs the DEPLOYED v2 pick engine (A' starts-weighted) client-side, with
zero network / server / model at pick time. Millisecond re-ranking as players
come off the board.

Architecture bright line (project record): the pick-time number is computed by
CODE, never a model. This builder bakes the live valuation + P(starts) table +
DEPLOYED_PARAMS into the page and ports `ffi.sim.strategy.evaluate_rules`'s A'
path to JS. A build-time GOLDEN TRACE from the PYTHON engine (a scripted ~19-
round draft) is embedded; the JS engine replays it on load and shows a green
badge on exact match or a RED "engine drift" banner on any mismatch -- no
silent fallback.

Shares the board data-shape / column order with cheat_sheet_html (ORDER); the
console's interaction layer (draft-other vs mine, suggestion panel, undo, export,
drift guard) is genuinely different and lives here.

    uv run python scripts/draft_console.py   ->  reports/draft-console.html
"""
from __future__ import annotations

import datetime
import html
import json
import subprocess
from pathlib import Path

from cheat_sheet_html import ORDER  # shared column order (RB,WR,QB,TE,DEF,K)
from ffi.db import connect
from ffi.scoring.config import load_config_v1
from ffi.sim.draft import ROUNDS, TEAMS, _avail_view, _build_sorted_pool, run_draft
from ffi.sim.pool import build_pool
from ffi.sim.priors import build_slot_priors
from ffi.sim.strategy import (
    DEPLOYED_PARAMS,
    adp_sort_key,
    evaluate_rules,
    make_strategy_fn,
    rule4_candidates,
)
from ffi.sim.opponent import CAND_WINDOW, STARTERS
from ffi.valuation.starts import CANONICAL_TABLE_PATH, load_starts_table

REPO_ROOT = Path(__file__).resolve().parents[1]

# Engine pool depth per position: deep enough that a full 228-pick draft never
# exhausts a position (run_draft would raise) and the top-CAND_WINDOW candidate
# window is never truncated. The board DISPLAYS only the shallower DISPLAY_DEPTH.
ENGINE_DEPTH = {"QB": 40, "RB": 100, "WR": 110, "TE": 40, "DEF": 20, "K": 20}
DISPLAY_DEPTH = {"QB": 30, "RB": 55, "WR": 60, "TE": 26, "DEF": 16, "K": 15}
GOLDEN_SLOT = 5  # the scripted-draft seat for the drift-guard golden trace
GOLDEN_SEED = 20260722

PLAYBOOK = (
    "RB scarce (steep cliff) — draft early. · WR deep & flat — wait, get volume. · "
    "QB deep — get 2 startable (forced R2/R5), QB3 R14+. · TE: 1 starter + 1 backup. · "
    "K/DEF last. · The panel is the deployed A′ engine; take its #1 unless you have a read."
)


# ---------------------------------------------------------------------------
# Baked pool
# ---------------------------------------------------------------------------


def engine_pool(pool):
    """Top-ENGINE_DEPTH players per position as plain dicts, sorted by proj desc
    (display order); the JS engine re-sorts each position by the draft's
    avail_by_pos key (adp asc, None last, then proj desc)."""
    out = {}
    for pos in ORDER:
        ranked = sorted(
            (p for p in pool if p.position == pos), key=lambda p: -p.proj_points
        )
        out[pos] = [
            {
                "id": p.ref,
                "n": p.name,
                "pos": p.position,
                "proj": p.proj_points,  # full precision -> identical double in JS
                "vorp": p.vorp,  # full precision: weight*vorp must match Python
                "t": p.tier,
                "adp": round(p.adp) if p.adp is not None else None,
            }
            for p in ranked[: ENGINE_DEPTH[pos]]
        ]
    return out


def restricted_pool(pool):
    """The PoolPlayer subset actually baked (top ENGINE_DEPTH/pos) -- the Python
    golden trace must run over EXACTLY this set so JS (which only has this set)
    reproduces it."""
    keep = set()
    for pos in ORDER:
        ranked = sorted(
            (p for p in pool if p.position == pos), key=lambda p: -p.proj_points
        )
        keep.update(p.ref for p in ranked[: ENGINE_DEPTH[pos]])
    return [p for p in pool if p.ref in keep]


# ---------------------------------------------------------------------------
# Suggestions (wraps the DEPLOYED engine) + golden trace
# ---------------------------------------------------------------------------


def _signature(rec: dict, top5: list) -> str:
    """Compact per-pick fingerprint compared byte-for-byte between the Python
    golden trace and the JS replay: recommended ref|rule then each top-5
    ref:score (3dp). Scores are IEEE-754 doubles identical in Python and JS."""
    body = ",".join(f"{t['id']}:{t['raw']:.3f}" for t in top5)
    return f"{rec['id']}|{rec['rule']}||{body}"


def console_suggestions(avail_by_pos, round_, counts, picks_left_after) -> dict:
    """The panel state at one of MY picks: the deployed pick (`recommended`,
    with the rule that fired) + the top-5 rule-4 value candidates
    (P_start[pos][k+1] x vorp), sorted deterministically like `_pick_best`."""
    pick, rule = evaluate_rules(
        avail_by_pos, round_, counts, picks_left_after, DEPLOYED_PARAMS
    )
    scored = rule4_candidates(
        avail_by_pos, round_, counts, picks_left_after, DEPLOYED_PARAMS
    )
    scored.sort(key=lambda sp: (-sp[0], adp_sort_key(sp[1]), sp[1].name))
    top5 = [
        {
            "id": p.ref,
            "n": p.name,
            "pos": p.position,
            "score": round(float(s), 3),
            "raw": float(s),  # full precision, for the drift-guard signature
            "adp": round(p.adp) if p.adp is not None else None,
        }
        for s, p in scored[:5]
    ]
    rec = {"id": pick.ref, "n": pick.name, "pos": pick.position, "rule": rule}
    return {
        "round": round_,
        "counts": dict(counts),
        "recommended": rec,
        "top5": top5,
        "signature": _signature(rec, top5),
    }


def generate_golden(pool, priors) -> tuple:
    """Run a real deployed draft (our seat pinned to GOLDEN_SLOT) over the baked
    pool, and record (removals, golden) where `removals` is the ordered
    [{id, mine}] every player leaves the board in, and `golden` is the panel
    state at each of MY 19 picks. The recommended pick at each state IS the
    deployed pick that run_draft made (self-consistency baked in)."""
    res = run_draft(
        pool,
        priors,
        make_strategy_fn(DEPLOYED_PARAMS),
        seed=GOLDEN_SEED,
        our_franchise_slot=GOLDEN_SLOT,
        our_position=GOLDEN_SLOT,
    )
    sorted_pool = _build_sorted_pool(pool)
    taken: set = set()
    counts: dict = {}
    removals, golden = [], []
    for pk in sorted(res.picks, key=lambda p: p["overall"]):
        mine = pk["position_slot"] == res.our_position
        if mine:
            round_ = len(golden) + 1  # our k-th pick is in round k
            picks_left_after = ROUNDS - round_
            avail = _avail_view(sorted_pool, taken)
            sug = console_suggestions(avail, round_, counts, picks_left_after)
            if sug["recommended"]["id"] != pk["ref"]:
                raise ValueError(
                    f"golden trace inconsistency at my pick {round_}: engine "
                    f"recommended {sug['recommended']['id']} but draft took {pk['ref']}"
                )
            golden.append(sug)
            counts[pk["pos"]] = counts.get(pk["pos"], 0) + 1
        removals.append({"id": pk["ref"], "mine": mine})
        taken.add(pk["ref"])
    return removals, golden


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def git_sha() -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    sha = r.stdout.strip() or "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    ).stdout.strip()
    return f"{sha}{'-dirty' if dirty else ''}"


def valuation_snapshot(conn) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max((params->>'snapshot_id')::int) FROM valuation.player_value "
            "WHERE scenario='qb_hoard_12' AND config_version=%s",
            (load_config_v1().version,),
        )
        row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def deployed_meta(table: dict) -> dict:
    caps = {p: c for p, c in DEPLOYED_PARAMS.caps}
    weights = {
        pos: [v for _, v in [(s, table[pos][s]) for s in sorted(table[pos])]]
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF")
        if pos in table
    }
    return {
        "qb_by_round": list(DEPLOYED_PARAMS.qb_by_round),
        "qb_not_before": list(DEPLOYED_PARAMS.qb_not_before),
        "defk_round": DEPLOYED_PARAMS.defk_round,
        "caps": caps,
        "starters": STARTERS,
        "positions": ["QB", "RB", "WR", "TE", "K", "DEF"],
        "cand_window": CAND_WINDOW,
        "rounds": ROUNDS,
        "teams": TEAMS,
        "roster_shape": "2QB/2RB/3WR/1TE/1FLEX/1K/1DEF/8BN (19 rounds)",
        "pstart_weights": weights,
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_page() -> tuple[str, dict]:
    conn = connect()
    table = load_starts_table(CANONICAL_TABLE_PATH)  # fail-loud on mode mismatch
    pool = build_pool(conn, "qb_hoard_12")
    baked = restricted_pool(pool)
    priors = build_slot_priors(conn)
    removals, golden = generate_golden(baked, priors)

    meta = {
        "date": datetime.date.today().isoformat(),
        "git_sha": git_sha(),
        "valuation_snapshot_id": valuation_snapshot(conn),
        "pstart_meta": table["_meta"],
        "deployed": deployed_meta(table),
        "golden_slot": GOLDEN_SLOT,
        "display_depth": DISPLAY_DEPTH,
    }
    baked_json = {
        "META": meta,
        "POOL": engine_pool(baked),
        "ORDER": ORDER,
        "REMOVALS": removals,
        "GOLDEN": golden,
    }
    page = (
        PAGE.replace("/*__BAKE__*/", json.dumps(baked_json))
        .replace("{play}", html.escape(PLAYBOOK))
        .replace("{date}", meta["date"])
    )
    summary = {
        "golden_picks": len(golden),
        "my_pick_positions": [g["recommended"]["pos"] for g in golden],
        "my_pick_rules": [g["recommended"]["rule"] for g in golden],
        "qb_rounds": [g["round"] for g in golden if g["recommended"]["pos"] == "QB"],
        "te_count": sum(1 for g in golden if g["recommended"]["pos"] == "TE"),
        "pick1_top5": golden[0]["top5"] if golden else [],
    }
    return page, summary


PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Live Draft Console — {date}</title>
<style>
:root{--bg:#0f1419;--card:#1a2029;--ink:#e6edf3;--dim:#8b98a5;--acc:#1f6feb;
--t1:#2ea043;--t2:#1f6feb;--t3:#8957e5;--t4:#9e6a03;--t5:#6e7681;--t6:#484f58;
--ok:#2ea043;--bad:#f85149;--warn:#d29922;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.35 -apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;z-index:6;background:#0b0f14;padding:8px 12px;border-bottom:1px solid #222}
h1{margin:0 0 3px;font-size:15px}
.play{color:var(--dim);font-size:11px;margin-bottom:6px}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input{flex:1;min-width:180px;max-width:320px;background:var(--card);border:1px solid #30363d;color:var(--ink);padding:6px 10px;border-radius:6px;font-size:13px}
button{background:var(--card);border:1px solid #30363d;color:var(--ink);padding:6px 10px;border-radius:6px;cursor:pointer}
button:hover{border-color:#58a6ff}
.cnt{color:var(--dim);font-size:11px}
#badge{font-size:11px;padding:3px 8px;border-radius:6px;font-weight:600}
#badge.ok{background:#0d2b17;color:var(--ok);border:1px solid var(--ok)}
#badge.bad{background:#3a0d0d;color:var(--bad);border:1px solid var(--bad)}
#driftbanner{display:none;background:var(--bad);color:#fff;padding:8px 12px;font-weight:700;text-align:center}
#driftbanner.show{display:block}
.wrap{display:flex;gap:8px;padding:8px;align-items:flex-start}
.cols{display:flex;gap:8px;overflow-x:auto;align-items:flex-start;flex:1}
.col{background:var(--card);border-radius:8px;min-width:200px;flex:1;overflow:hidden}
.col h2{margin:0;font-size:12px;padding:6px 8px;background:#11161d;position:sticky;top:0}
.col .list{max-height:78vh;overflow:auto}
.row{display:flex;align-items:center;gap:5px;padding:3px 8px;border-left:3px solid var(--t6);cursor:pointer}
.row:hover{background:#222b36}
.row.d{opacity:.3;text-decoration:line-through}
.row.mine{background:#0d2b17}
.gone .nm{color:var(--warn)}
.rk{color:var(--dim);width:20px;text-align:right;font-variant-numeric:tabular-nums}
.nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.num{color:var(--dim);font-size:11px;font-variant-numeric:tabular-nums}
.adp{width:32px;text-align:right}
.mp{flex:none;width:26px;padding:1px 0;text-align:center;color:var(--acc);font-weight:700;cursor:pointer;border:1px solid #30363d;border-radius:5px;background:#11161d;font-size:13px}
.mp:hover{border-color:var(--acc);background:#182231}
.t1{border-left-color:var(--t1)}.t2{border-left-color:var(--t2)}.t3{border-left-color:var(--t3)}
.t4{border-left-color:var(--t4)}.t5{border-left-color:var(--t5)}.t6{border-left-color:var(--t6)}
.hi{background:#243b53}
#panel{width:320px;flex:none;background:var(--card);border-radius:8px;padding:10px;position:sticky;top:56px;max-height:calc(100vh - 64px);overflow:auto}
#panel h3{margin:0 0 6px;font-size:13px}
.status{font-size:12px;color:var(--dim);margin-bottom:8px}
.status b{color:var(--ink)}
.rhead{font-size:12px;font-weight:600;margin:12px 0 4px;border-top:1px solid #222;padding-top:8px}
.rs{display:flex;align-items:center;gap:8px;padding:3px 6px;border-radius:5px;font-size:12.5px;margin-bottom:1px}
.rs.filled{background:#11161d}
.rs.empty{opacity:.45}
.rslot{width:42px;flex:none;color:var(--acc);font-weight:600;font-size:11px;letter-spacing:.02em}
.rs.bn .rslot{color:var(--dim)}
.rname{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rrd{color:var(--dim);font-size:11px;font-variant-numeric:tabular-nums}
.sug{display:flex;align-items:baseline;gap:6px;padding:4px 6px;border-radius:6px;margin-bottom:3px;background:#11161d}
.sug.rec{outline:1px solid var(--acc)}
.sug .r{color:var(--dim);width:14px}
.sug .s{margin-left:auto;color:var(--ink);font-variant-numeric:tabular-nums}
.sug .p{color:var(--dim);font-size:11px}
.sug .g{color:var(--warn);font-size:10px}
.rule{font-size:10px;color:var(--acc);text-transform:uppercase}
.myroster{margin-top:10px;font-size:11px;color:var(--dim)}
.prov{font-size:10px;color:var(--t6);margin-top:10px;line-height:1.4}
kbd{background:#11161d;border:1px solid #30363d;border-radius:4px;padding:0 4px;font-size:10px}
</style></head><body>
<div id=driftbanner>⚠ ENGINE DRIFT — do not trust suggestions. The JS engine disagrees with the Python golden trace.</div>
<header>
<h1>Live Draft Console — {date} <span class=cnt>· deployed A′ engine · our scoring</span> <span id=badge></span></h1>
<div class=play>{play}</div>
<div class=bar>
<span id=setup></span>
<input id=q placeholder="search a name → Enter = drafted by other">
<button onclick=undo()>Undo</button>
<button onclick=resetAll()>Reset</button>
<button onclick=exportJSON()>Export JSON</button>
<span class=cnt id=cnt></span>
</div></header>
<div class=wrap>
<div id=panel>
<h3>Suggestions <span class=rule id=recrule></span></h3>
<div class=status id=status></div>
<div id=sugs></div>
<div class=myroster id=myroster></div>
<div class=prov id=prov></div>
</div>
<div class=cols id=cols></div>
</div>
<script>
const BAKE=/*__BAKE__*/;
const {META,POOL,ORDER,REMOVALS,GOLDEN}=BAKE;
const D=META.deployed, W=D.pstart_weights, ST=D.starters, POS=D.positions;
const CAPS=D.caps, QBR=D.qb_by_round, QNB=D.qb_not_before, DEFK=D.defk_round;
const CW=D.cand_window, RN=D.rounds, TEAMSN=D.teams;

// ---- engine (faithful port of ffi.sim.strategy evaluate_rules, A' path) ----
function reqPicks(c){
  let need=0; for(const p in ST) need+=Math.max(0,ST[p]-(c[p]||0));
  const flex=Math.max(0,(c.RB||0)-2)+Math.max(0,(c.WR||0)-3)+Math.max(0,(c.TE||0)-1);
  return need+(flex>=1?0:1);
}
function feasible(c,pos,pla){const c2={...c};c2[pos]=(c2[pos]||0)+1;return reqPicks(c2)<=pla;}
function unmet(c){const b=reqPicks(c),u=[];for(const p in ST){const c2={...c};c2[p]=(c2[p]||0)+1;if(reqPicks(c2)<b)u.push(p);}return u;}
function pw(pos,slot){const r=W[pos];if(!r)return 0;return (slot>=1&&slot<=r.length)?r[slot-1]:0;}
function adpKey(p){return [p.adp==null?1:0,p.adp==null?0:p.adp];}
// argmax by (-score, adp None-last, adp asc, name) — matches _pick_best
function pickBest(scored){
  let best=null,bk=null;
  for(const [s,p] of scored){
    const k=[-s,p.adp==null?1:0,p.adp==null?0:p.adp,p.n];
    if(bk==null||cmp(k,bk)<0){bk=k;best=p;}
  }
  return best;
}
function cmp(a,b){for(let i=0;i<a.length;i++){if(a[i]<b[i])return -1;if(a[i]>b[i])return 1;}return 0;}
function availByPos(taken){
  const o={};
  for(const pos of POS){
    const list=(POOL[pos]||[]).filter(p=>!taken.has(p.id));
    list.sort((x,y)=>cmp(adpKey(x).concat([-x.proj]),adpKey(y).concat([-y.proj])));
    o[pos]=list;
  }
  return o;
}
function rule4(round_,c,pla,avail){
  const qbn=c.QB||0,scored=[];
  for(const pos of POS){
    if(pos==='DEF'||pos==='K')continue;                    // A': DEF/K not voluntary
    if(pos==='QB'&&(c.QB||0)>=QBR.length)continue;          // plan done -> no QB4
    if(pos==='QB'&&qbn<QNB.length&&round_<QNB[qbn])continue;// qb_not_before (inert)
    if((c[pos]||0)>=(CAPS[pos]??1e9))continue;             // caps (TE<=2)
    if(!feasible(c,pos,pla))continue;
    const cands=avail[pos]||[]; if(!cands.length)continue;
    const w=pw(pos,(c[pos]||0)+1); if(w<=0)continue;        // never-start dropped
    for(const p of cands.slice(0,CW))scored.push([w*p.vorp,p]);
  }
  return scored;
}
function evaluate(round_,c,pla,avail){
  // 1 feasibility force
  if(reqPicks(c)===pla){
    const scored=[];
    for(const pos of unmet(c)){
      const cands=avail[pos]||[],w=pw(pos,(c[pos]||0)+1);
      for(const p of cands.slice(0,CW))scored.push([w*p.vorp,p]);
    }
    if(scored.length)return {player:pickBest(scored),rule:'feasibility'};
  }
  // 2 QB deadline force (smallest unmet n)
  const qbn=c.QB||0;
  for(let n=qbn+1;n<=QBR.length;n++){
    if(round_>=QBR[n-1]){
      const cands=avail.QB||[];
      if(cands.length&&qbn<(CAPS.QB??1e9)&&feasible(c,'QB',pla))
        return {player:pickBest(cands.slice(0,CW).map(p=>[p.vorp,p])),rule:'qb_deadline'};
      break;
    }
  }
  // 3 DEF then K force
  if(round_>=DEFK&&(c.DEF||0)===0&&(c.DEF||0)<(CAPS.DEF??1e9)){
    const cands=avail.DEF||[];
    if(cands.length&&feasible(c,'DEF',pla))
      return {player:pickBest(cands.slice(0,CW).map(p=>[p.vorp,p])),rule:'defk'};
  }
  if(round_>=DEFK+1&&(c.K||0)===0&&(c.K||0)<(CAPS.K??1e9)){
    const cands=avail.K||[];
    if(cands.length&&feasible(c,'K',pla))
      return {player:pickBest(cands.slice(0,CW).map(p=>[p.vorp,p])),rule:'defk'};
  }
  // 4 value
  const scored=rule4(round_,c,pla,avail);
  if(!scored.length)throw new Error('engine: no feasible candidate');
  return {player:pickBest(scored),rule:'value'};
}
function suggestions(round_,c,pla,taken){
  const avail=availByPos(taken);
  const {player,rule}=evaluate(round_,c,pla,avail);
  const scored=rule4(round_,c,pla,avail);
  scored.sort((a,b)=>cmp([-a[0],a[1].adp==null?1:0,a[1].adp==null?0:a[1].adp,a[1].n],
                         [-b[0],b[1].adp==null?1:0,b[1].adp==null?0:b[1].adp,b[1].n]));
  const top5=scored.slice(0,5).map(([s,p])=>({id:p.id,n:p.n,pos:p.pos,score:Math.round(s*1000)/1000,raw:s,adp:p.adp}));
  const rec={id:player.id,n:player.n,pos:player.pos,rule};
  const sig=rec.id+'|'+rec.rule+'||'+top5.map(t=>t.id+':'+t.raw.toFixed(3)).join(',');
  return {recommended:rec,top5,signature:sig};
}

// ---- drift guard: replay the embedded golden trace ----
function selfTest(){
  const taken=new Set(); const c={}; let myPick=0; let ok=true; const fails=[];
  for(const rem of REMOVALS){
    if(rem.mine){
      const round_=myPick+1, pla=RN-round_;
      const got=suggestions(round_,c,pla,taken);
      const want=GOLDEN[myPick];
      if(!want||got.signature!==want.signature){ok=false;fails.push({pick:round_,got:got.signature,want:want&&want.signature});}
      // advance my counts using the golden's recommended pos (== deployed pick)
      const pos=(want?want.recommended.pos:got.recommended.pos);
      c[pos]=(c[pos]||0)+1; myPick++;
    }
    taken.add(rem.id);
  }
  const badge=document.getElementById('badge');
  if(ok){badge.className='ok';badge.textContent='✓ self-test '+GOLDEN.length+'/'+GOLDEN.length;}
  else{badge.className='bad';badge.textContent='✗ ENGINE DRIFT';
       document.getElementById('driftbanner').classList.add('show');
       console.error('drift-guard mismatches',fails);}
  return ok;
}

// ---- live state ----
// marks: id -> 'gone'|'mine'. order: the sequence marks were made (export order
// + undo LIFO). Persisted under a key VERSIONED BY THE BUILD git SHA, so a
// rebuilt console can never load stale, structurally-incompatible state.
const SKEY='dc:'+META.git_sha;
let mySlot=0,marks={},order=[];
(function(){try{const s=JSON.parse(localStorage.getItem(SKEY)||'null');
  if(s){mySlot=s.slot||0;marks=s.marks||{};order=Array.isArray(s.order)?s.order:[];}}catch(e){}})();
const byId={}; for(const pos of ORDER)for(const p of POOL[pos])byId[p.id]=p;
function persist(){localStorage.setItem(SKEY,JSON.stringify({slot:mySlot,marks,order}));}

function seatOf(overall){const r=Math.floor((overall-1)/TEAMSN)+1,i=(overall-1)%TEAMSN;return r%2===1?i+1:TEAMSN-i;}
function mineOveralls(){const a=[];for(let o=1;o<=RN*TEAMSN;o++)if(seatOf(o)===mySlot)a.push(o);return a;}
function state(){
  const taken=new Set(order),c={};let made=0;
  for(const id of order)if(marks[id]==='mine'){const p=byId[id];if(p){c[p.pos]=(c[p.pos]||0)+1;made++;}}
  return {taken,c,made};
}
// whole-row click: cross off (toggle). ALWAYS works -- no turn gating, so a
// manager who joins mid-draft marks everyone already gone in seconds. The
// current overall pick is INFERRED from order.length, never blocked on.
function toggleGone(id){
  if(marks[id]){delete marks[id];const i=order.indexOf(id);if(i>=0)order.splice(i,1);}
  else{marks[id]='gone';order.push(id);}
  persist();render();
}
// separate ＋ button (stopPropagation'd, right edge): MY pick -> advances counts.
function draftMine(id){if(!marks[id])order.push(id);marks[id]='mine';persist();render();}
function undo(){const id=order.pop();if(id!==undefined){delete marks[id];persist();render();}}
function resetAll(){if(confirm('Reset the whole board? Clears all marks.')){marks={};order=[];persist();render();}}
function setSlot(v){mySlot=parseInt(v)||0;persist();render();}
function renderSetup(){
  let opts='<option value=0>slot…</option>';
  for(let i=1;i<=TEAMSN;i++)opts+='<option value='+i+(i===mySlot?' selected':'')+'>slot '+i+'</option>';
  document.getElementById('setup').innerHTML='<span class=cnt>My draft slot </span><select onchange="setSlot(this.value)">'+opts+'</select>';
}

function render(){
  const {taken,c,made}=state();
  // board
  const cols=document.getElementById('cols');cols.innerHTML='';
  for(const pos of ORDER){
    const col=document.createElement('div');col.className='col';
    let h='<h2>'+pos+' <span class=cnt>('+ST[pos]+' start)</span></h2><div class=list>';
    let rk=0;const dep=META.display_depth[pos]||30;let availShown=0;
    for(const p of POOL[pos]){
      const status=marks[p.id];const isAvail=!status;   // undefined | 'gone' | 'mine'
      if(isAvail){if(availShown>=dep)break;availShown++;rk++;}  // taken rows still render (toggleable)
      const adp=p.adp==null?'—':p.adp;
      const cls='row t'+p.t+(status==='mine'?' mine':status==='gone'?' d':'');
      h+='<div class="'+cls+'" data-n="'+p.n.toLowerCase()+'" onclick="toggleGone(\''+p.id+'\')" title="click = cross off (toggle)">'+
         '<span class=rk>'+(isAvail?rk:'·')+'</span>'+
         '<span class=nm>'+p.n+'</span>'+
         '<span class="num">'+Math.round(p.proj)+'</span>'+
         '<span class="num adp">'+adp+'</span>'+
         '<button class=mp title="MY pick (adds to your roster)" onclick="draftMine(\''+p.id+'\');event.stopPropagation()">＋</button>'+
         '</div>';
    }
    col.innerHTML=h+'</div>';cols.appendChild(col);
  }
  document.getElementById('cnt').textContent=taken.size+' off · '+made+' mine';
  // panel
  renderPanel(taken,c,made);
}

function renderPanel(taken,c,made){
  const sugs=document.getElementById('sugs');
  const status=document.getElementById('status');
  const recrule=document.getElementById('recrule');
  if(!mySlot){status.innerHTML='Pick your draft slot to start.';sugs.innerHTML='';recrule.textContent='';return;}
  if(made>=RN){status.innerHTML='<b>Roster full</b> (19 picks).';sugs.innerHTML='';recrule.textContent='';renderRoster();return;}
  const round_=made+1,pla=RN-round_;
  const currentOverall=taken.size+1;
  const mo=mineOveralls();const nextPick=mo[made];       // my upcoming pick overall
  const picksUntil=nextPick-currentOverall;
  const isMyTurn=picksUntil<=0;
  const goneThreshold=isMyTurn?(mo[made+1]??300):nextPick;
  let s;
  try{s=suggestions(round_,c,pla,taken);}
  catch(e){status.innerHTML='<b style=color:var(--bad)>'+e.message+'</b>';sugs.innerHTML='';return;}
  recrule.textContent=s.recommended.rule;
  const turn=isMyTurn?'<b style=color:var(--ok)>YOUR PICK NOW</b>':('your next pick in <b>'+picksUntil+'</b> picks');
  status.innerHTML='Round <b>'+round_+'</b> · overall <b>'+currentOverall+'</b> · '+turn+
    ' · next-pick overall <b>'+goneThreshold+'</b>';
  let h='';let i=0;
  for(const t of s.top5){
    i++;const rec=t.id===s.recommended.id?' rec':'';
    const gone=(t.adp!=null&&t.adp<=goneThreshold)?'<span class=g> likely gone</span>':'';
    h+='<div class="sug'+rec+'"><span class=r>'+i+'</span>'+
       '<span>'+t.n+' <span class=p>'+t.pos+(t.adp!=null?' · adp '+t.adp:'')+'</span>'+gone+'</span>'+
       '<span class=s>'+t.score.toFixed(1)+'</span></div>';
  }
  // if recommended is a force pick not in top5, surface it explicitly
  if(!s.top5.some(t=>t.id===s.recommended.id)){
    h='<div class="sug rec"><span class=r>★</span><span>'+s.recommended.n+
      ' <span class=p>'+s.recommended.pos+' · FORCE ('+s.recommended.rule+')</span></span></div>'+h;
  }
  sugs.innerHTML=h;
  renderRoster();
}
// League slot grid (from the baked roster shape): QB/QB/RB/RB/WR/WR/WR/TE/FLEX/
// K/DEF + bench. Filled by projection (starters first, best leftover RB/WR/TE ->
// FLEX, rest -> bench). Recomputed from state -- no new state.
const SLOT_SPEC=[['QB',ST.QB],['RB',ST.RB],['WR',ST.WR],['TE',ST.TE],['FLEX',1],['K',ST.K],['DEF',ST.DEF]];
const FLEX_ELIG=['RB','WR','TE'];
function assignRoster(){
  const mine=[];let r=0;                       // my picks in draft order; round = pick #
  for(const id of order)if(marks[id]==='mine'){r++;const p=byId[id];if(p)mine.push({...p,round:r});}
  const byPos={};for(const m of mine)(byPos[m.pos]=byPos[m.pos]||[]).push(m);
  for(const k in byPos)byPos[k].sort((a,b)=>b.proj-a.proj);
  const used=new Set(),slots=[];
  for(const [pos,n] of SLOT_SPEC){
    if(pos==='FLEX'){
      let best=null;
      for(const fp of FLEX_ELIG)for(const m of (byPos[fp]||[]))
        if(!used.has(m.id)&&(!best||m.proj>best.proj))best=m;
      if(best)used.add(best.id);
      slots.push({slot:'FLEX',player:best});continue;
    }
    for(let i=0;i<n;i++){
      const pick=(byPos[pos]||[]).find(m=>!used.has(m.id))||null;
      if(pick)used.add(pick.id);
      slots.push({slot:pos,player:pick});
    }
  }
  const bench=mine.filter(m=>!used.has(m.id));  // remaining, draft order
  const nb=RN-slots.length;                     // fixed count (RN=19 - 11 starters = 8)
  for(let i=0;i<nb;i++)slots.push({slot:'BN',player:bench[i]||null});
  return slots;
}
function renderRoster(){
  let h='<div class=rhead>My roster</div>';
  for(const a of assignRoster()){
    const bn=a.slot==='BN'?' bn':'';
    if(a.player)
      h+='<div class="rs filled'+bn+'"><span class=rslot>'+a.slot+'</span>'+
         '<span class=rname>'+a.player.n+'</span><span class=rrd>R'+a.player.round+'</span></div>';
    else
      h+='<div class="rs empty'+bn+'"><span class=rslot>'+a.slot+'</span><span class=rname>—</span></div>';
  }
  document.getElementById('myroster').innerHTML=h;
}

function exportJSON(){
  const picks=order.map((id,i)=>({overall:i+1,name:byId[id]?byId[id].n:id,ref:id,mine:marks[id]==='mine'}));
  const out={format:'draft_console_v1',my_slot:mySlot,teams:TEAMSN,rounds:RN,picks,
    provenance:{date:META.date,git_sha:META.git_sha,valuation_snapshot_id:META.valuation_snapshot_id,
      pstart_meta:META.pstart_meta,deployed_qb_by_round:QBR,defk_round:DEFK}};
  const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});
  const u=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=u;a.download='draft-'+META.date+'.json';a.click();URL.revokeObjectURL(u);
}

// search
const q=document.getElementById('q');
q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();
  document.querySelectorAll('.row').forEach(r=>r.classList.toggle('hi',v&&r.dataset.n.includes(v)));});
q.addEventListener('keydown',e=>{if(e.key!=='Enter')return;const v=q.value.trim().toLowerCase();if(!v)return;
  for(const pos of ORDER)for(const p of POOL[pos])
    if(!marks[p.id]&&p.n.toLowerCase().includes(v)){toggleGone(p.id);q.value='';document.querySelectorAll('.row').forEach(r=>r.classList.remove('hi'));return;}});

// provenance footer
document.getElementById('prov').innerHTML=
  'valuation snapshot #'+META.valuation_snapshot_id+' · p_starts '+META.pstart_meta.mode+' seed '+META.pstart_meta.seed+
  '<br>DEPLOYED qb_by_round '+JSON.stringify(QBR)+' · defk R'+DEFK+' · TE cap '+CAPS.TE+
  '<br>git '+META.git_sha+' · golden slot '+META.golden_slot+
  '<br><kbd>click row</kbd> = crossed off (toggle) · <kbd>＋</kbd> = my pick';

// init (state already loaded from the SHA-versioned key above)
renderSetup();selfTest();render();
</script></body></html>"""


def main():
    page, summary = build_page()
    out = REPO_ROOT / "reports" / "draft-console.html"
    out.write_text(page)
    print(f"wrote {out}")
    print(
        f"golden trace: {summary['golden_picks']} my-picks; "
        f"QB rounds {summary['qb_rounds']}; TE count {summary['te_count']}"
    )
    print(f"my-pick positions: {summary['my_pick_positions']}")
    print(f"my-pick rules:     {summary['my_pick_rules']}")


if __name__ == "__main__":
    main()
