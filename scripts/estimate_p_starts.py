#!/usr/bin/env python3
"""Estimate P_start[pos][slot] = fraction of regular-season weeks the slot-th-best
player at a position (by projection) is pressed into the optimal weekly lineup,
via Monte-Carlo over AVAILABILITY (byes + a simple injury model).

Phase A of value = VORP x P(starts). Key modeling choice: lineups are set by
PROJECTION rank among AVAILABLE players (that's how a manager actually starts a
lineup), so P(starts) is driven by who is OUT that week -- not by weekly scoring
noise. The slot-N player starts when fewer than `need` players ranked above him
are available (a base starter) or when he wins the single FLEX slot (best
available leftover RB/WR/TE by projection).

    uv run python scripts/estimate_p_starts.py [--seasons 2000] [--no-injuries]
"""
import argparse
import datetime
import json
from dataclasses import dataclass

import numpy as np

from ffi.db import connect
from ffi.league_profile import get_profile
from ffi.sim.season import BYE_WINDOW, FLEX_POS, REG_WEEKS

DEPTH = {"QB": 4, "RB": 7, "WR": 8, "TE": 3, "K": 2, "DEF": 2}
# games missed / season by position (rough assumption, tunable; DEF ~never sits).
INJURY_LAMBDA = {"QB": 1.5, "RB": 2.5, "WR": 1.8, "TE": 1.8, "K": 0.3, "DEF": 0.0}


@dataclass(frozen=True)
class _P:
    position: str
    proj_points: float


def _load_players(conn, config_version: int) -> list[_P]:
    """Read scored season projections (position, points) directly, so the
    P(starts) table can be generated BEFORE valuation exists — breaking the
    bootstrap cycle (valuation needs this table, which used to need the pool,
    which needs valuation). P(starts) only depends on projection rank + roster
    shape, not on VORP/ADP."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT x.position, pp.points::float
            FROM scoring.projection_points pp
            JOIN public.player_id_xwalk x ON x.sleeper_id = pp.player_ref
            WHERE pp.source = 'sleeper' AND pp.horizon = 'season'
              AND pp.config_version = %s
              AND pp.snapshot_id = (SELECT max(snapshot_id) FROM raw.sleeper_projections WHERE week IS NULL)
              AND x.position IN ('QB','RB','WR','TE','K','PK','DEF')
            """,
            (config_version,),
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(
            f"no scored season projections for config_version={config_version} — "
            "run scripts/score_sleeper_projections.py --config-version <v> first"
        )
    out = []
    for pos, pts in rows:
        pos = "K" if pos == "PK" else pos
        out.append(_P(position=pos, proj_points=float(pts)))
    return out


def draw_availability(players, n_seasons, seed, injuries):
    """(n_seasons, REG_WEEKS, P) bool -- available unless on bye or injured."""
    rng = np.random.default_rng(seed)
    p = len(players)
    avail = np.ones((n_seasons, REG_WEEKS, p), dtype=bool)
    # one bye per player per season, uniform in BYE_WINDOW (weeks are 1-indexed)
    bye = rng.integers(BYE_WINDOW[0], BYE_WINDOW[1] + 1, size=(n_seasons, p))
    s = np.arange(n_seasons)[:, None]
    pcol = np.arange(p)[None, :]
    avail[s, bye - 1, pcol] = False
    if injuries:
        lam = np.array([INJURY_LAMBDA[q.position] for q in players])
        miss = rng.poisson(lam, size=(n_seasons, p)).clip(0, REG_WEEKS)
        for si in range(n_seasons):
            for pi in range(p):
                m = miss[si, pi]
                if m:
                    wk = rng.choice(REG_WEEKS, size=m, replace=False)
                    avail[si, wk, pi] = False
    return avail


def lineup_starts(avail, pos_idx, proj, starters):
    """(S,W,P) availability -> (P,) fraction of weeks each player starts."""
    s, w, p = avail.shape
    starts = np.zeros((s, w, p), dtype=bool)
    for pos, need in starters.items():
        idxs = np.array(pos_idx[pos])  # projection (=slot) order
        a = avail[..., idxs]
        cum = np.cumsum(a, axis=-1)  # rank among available at this position
        starts[..., idxs] |= a & (cum <= need)  # base starters
    # FLEX: best available leftover RB/WR/TE by projection
    flex_ok = np.zeros((s, w, p), dtype=bool)
    for pos in FLEX_POS:
        idxs = np.array(pos_idx[pos])
        flex_ok[..., idxs] |= avail[..., idxs] & ~starts[..., idxs]
    flex_val = np.where(flex_ok, proj[None, None, :], -1.0)
    winner = np.argmax(flex_val, axis=-1)  # (S,W)
    has = flex_ok.any(axis=-1)
    si, wi = np.where(has)
    starts[si, wi, winner[si, wi]] = True
    return starts.reshape(-1, p).mean(axis=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--profile", default="najee", help="league profile: najee (2-QB) or lmu (1-QB)"
    )
    ap.add_argument("--no-injuries", action="store_true")
    ap.add_argument(
        "--out",
        default=None,
        help="output path (default: reports/p_starts-<today>.json). Pass "
        "data/p_starts.json to write the tracked canonical table live code reads.",
    )
    args = ap.parse_args()

    profile = get_profile(args.profile)
    conn = connect()
    pool = _load_players(conn, profile.config_version)

    players, pos_idx, slot_of = [], {}, {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        ranked = sorted(
            (q for q in pool if q.position == pos), key=lambda q: -q.proj_points
        )
        pos_idx[pos] = []
        for slot, pl in enumerate(ranked[: DEPTH[pos]], start=1):
            slot_of[len(players)] = (pos, slot)
            pos_idx[pos].append(len(players))
            players.append(pl)
    proj = np.array([q.proj_points for q in players], dtype=float)

    avail = draw_availability(players, args.seasons, args.seed, not args.no_injuries)
    fracs = lineup_starts(avail, pos_idx, proj, profile.starters)

    mode = "byes-only" if args.no_injuries else "byes+injuries"
    table = {
        "_meta": {
            "mode": mode,
            "seed": args.seed,
            "seasons": args.seasons,
            "scenario": profile.scenario,
            "profile": profile.name,
            "generated": datetime.date.today().isoformat(),
        }
    }
    for gi, (pos, slot) in slot_of.items():
        table.setdefault(pos, {})[slot] = round(float(fracs[gi]), 3)

    inj = "byes only" if args.no_injuries else "byes + injuries"
    print(f"P_start[pos][slot]  ({args.seasons} MC seasons, {inj})\n")
    print("  slot:  " + "".join(f"{s:>7}" for s in range(1, 9)))
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        row = "".join(f"{table[pos].get(s, ''):>7}" for s in range(1, 9))
        print(f"  {pos:<5}{row}")

    path = args.out or f"reports/p_starts-{datetime.date.today().isoformat()}.json"
    with open(path, "w") as f:
        json.dump(table, f, indent=1)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
