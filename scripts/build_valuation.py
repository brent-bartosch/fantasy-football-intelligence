#!/usr/bin/env python3
"""Build valuation.player_value for the QB-hoarding scenario grid from the
latest season-level Sleeper projection points (imputed FD included — Sleeper
native FD rejected as ~2x inflated, Task 8/R16), tiered by GMM. Uncertainty
band v1 = FP ECR rank_std where a superflex ECR row joins (cache only), else
NULL. Provenance in params.

KNOWN CONSTRAINT (discovered Task 10): the FantasyPros public API key tier
hard-caps consensus-rankings at 10 players per position/OP-slot. The superflex
(OP) cache therefore only covers ~10 elite players — value_low/value_high will
be NULL for everyone else. This is accepted and disclosed loudly below; GMM
tiers run on OUR projected points, never on the truncated FP list.
"""
import json

from ffi.db import connect
from ffi.ingest.fantasypros import latest_fp_payload
from ffi.scoring.config import load_config_v1
from ffi.valuation.baseline import compute_baselines, compute_replacement_ranks
from ffi.valuation.tiers import gmm_tiers

SCENARIOS = {
    "qb_hoard_0": {"teams": 12, "qb_extra_rostered": 0},
    "qb_hoard_12": {"teams": 12, "qb_extra_rostered": 12},
    "qb_hoard_24": {"teams": 12, "qb_extra_rostered": 24},
}

conn = connect()
cfg = load_config_v1()

# latest sleeper season snapshot's scored points, joined to crosswalk + position
with conn.cursor() as cur:
    cur.execute(
        """
        SELECT x.xwalk_id, x.position, x.name, pp.points::float, pp.snapshot_id
        FROM scoring.projection_points pp
        JOIN public.player_id_xwalk x ON x.sleeper_id = pp.player_ref
        WHERE pp.source = 'sleeper' AND pp.horizon = 'season'
          AND pp.config_version = %s
          AND pp.snapshot_id = (SELECT max(snapshot_id) FROM raw.sleeper_projections WHERE week IS NULL)
          AND x.position IN ('QB','RB','WR','TE','K','PK')
        ORDER BY pp.points DESC
        """,
        (cfg.version,),
    )
    rows = cur.fetchall()
if not rows:
    raise SystemExit(
        "no scored season projections joined to crosswalk — run Tasks 5-7 first"
    )

# NOTE: DEF valuation deliberately absent from v1 board values — Task 12 answers
# draft-vs-stream for DEF/K first; K included for completeness.
# Kickers are position 'PK' in public.player_id_xwalk; normalize to 'K' so
# they land in the K pool instead of being silently dropped (Phase 3 Task 2 —
# K had zero valuation rows before this fix).
by_pos: dict[str, list] = {}
for xid, pos, name, pts, snap in rows:
    pos = "K" if pos == "PK" else pos
    by_pos.setdefault(pos, []).append((xid, name, pts))

fp_std = {}
fp = latest_fp_payload(conn, "consensus-rankings", {"position": "OP"})
fp_player_count = 0
if fp:
    for p in fp.get("players", []):
        # field names verified live in Task 10: player_name, rank_ecr, rank_std,
        # rank_min, rank_max, player_id (FP's own id).
        if "player_name" in p and "rank_std" in p:
            fp_std[p["player_name"].lower()] = float(p["rank_std"])
    fp_player_count = len(fp_std)
else:
    print(
        "WARNING: no superflex FP cache — value bands will be NULL (visible, not silent)"
    )

total_players = len(rows)
covered = sum(1 for _, _, name, _, _ in rows if name.lower() in fp_std)
print(
    f"FP ECR bands cover {covered}/{total_players} players "
    f"({fp_player_count} distinct FP superflex names cached; public-tier cap "
    "is 10 players/position — see Task 10 finding, this is expected and accepted)"
)

snapshot_id = rows[0][4]
with conn.cursor() as cur:
    for scen_name, scen in SCENARIOS.items():
        # Idempotent re-run: valuation is the CURRENT view (history is
        # recomputable from raw), so each rebuild fully replaces the
        # (config, scenario) slice regardless of snapshot_id — keying the
        # DELETE on snapshot_id let rows from earlier snapshots stack up as
        # the nightly chain advanced snapshots (Phase 3 Task 2 duplicate fix).
        cur.execute(
            "DELETE FROM valuation.replacement_baseline WHERE config_version=%s AND scenario=%s",
            (cfg.version, scen_name),
        )
        cur.execute(
            "DELETE FROM valuation.player_value WHERE config_version=%s AND scenario=%s",
            (cfg.version, scen_name),
        )

        ranks = compute_replacement_ranks(scen)
        ranks = {p: r for p, r in ranks.items() if p in by_pos}  # DEF absent in v1
        baselines = compute_baselines(
            {p: [pts for _, _, pts in by_pos[p]] for p in ranks}, ranks
        )
        for pos, base in baselines.items():
            cur.execute(
                """INSERT INTO valuation.replacement_baseline
                   (config_version, scenario, position, replacement_rank, replacement_points, params)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    cfg.version,
                    scen_name,
                    pos,
                    ranks[pos],
                    base,
                    json.dumps({**scen, "snapshot_id": snapshot_id}),
                ),
            )
        for pos in ranks:
            players = by_pos[pos]
            tiers = (
                gmm_tiers([pts for _, _, pts in players])
                if len(players) >= 4
                else [1] * len(players)
            )
            for (xid, name, pts), tier in zip(players, tiers):
                std = fp_std.get(name.lower())
                cur.execute(
                    """INSERT INTO valuation.player_value
                       (config_version, scenario, xwalk_id, position, proj_points, vorp,
                        tier, value_low, value_high, params)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        cfg.version,
                        scen_name,
                        xid,
                        pos,
                        pts,
                        pts - baselines[pos],
                        tier,
                        (pts - baselines[pos]) - (std or 0) * 1.0 if std else None,
                        (pts - baselines[pos]) + (std or 0) * 1.0 if std else None,
                        json.dumps({"snapshot_id": snapshot_id, "ecr_std_scale": 1.0}),
                    ),
                )
conn.commit()

# Post-build assertions (fail-loud): no stacked (player, scenario) duplicates,
# and the PK->K mapping actually landed rows in the K pool.
with conn.cursor() as cur:
    cur.execute(
        """SELECT count(*) FROM (SELECT xwalk_id, scenario FROM valuation.player_value
           WHERE config_version=%s GROUP BY 1,2 HAVING count(*) > 1) d""",
        (cfg.version,),
    )
    dups = cur.fetchone()[0]
    if dups:
        raise SystemExit(
            f"valuation.player_value has {dups} duplicated (player, scenario) rows after rebuild"
        )
    cur.execute(
        "SELECT count(*) FROM valuation.player_value WHERE config_version=%s AND position='K' AND scenario='qb_hoard_12'",
        (cfg.version,),
    )
    if cur.fetchone()[0] < 20:
        raise SystemExit("K missing from valuation — PK mapping regressed")

with conn.cursor() as cur:
    cur.execute(
        """SELECT v.scenario, x.name, v.position, round(v.vorp,1)
           FROM valuation.player_value v JOIN public.player_id_xwalk x USING (xwalk_id)
           WHERE v.scenario='qb_hoard_12' ORDER BY v.vorp DESC LIMIT 25"""
    )
    print("top 25 (qb_hoard_12):")
    for r in cur.fetchall():
        print("  ", r)
