"""Sleeper projection record -> StatLine. Allowlist mapping: every stats key
must be mapped, exact-ignored, or prefix-ignored — anything else is schema
drift and fails loud (R5: silent semantic drift is the worst case).

Vocabulary reconciled 2026-07-09 against the live season-level snapshot
(raw.sleeper_projections snapshot_id=3, season=2026, week=NULL, 3292 records)
via:
  SELECT DISTINCT k FROM raw.sleeper_projections p,
    jsonb_array_elements(p.payload) rec, jsonb_object_keys(rec->'stats') k
  WHERE p.week IS NULL ORDER BY 1;
See task-7-report.md for the full key list and per-key classification notes.
"""
from ffi.ingest.base import IngestError
from ffi.scoring.statline import StatLine

_SLEEPER_MAP = {
    "pass_cmp": "pass_completions",
    "pass_inc": "pass_incompletions",  # not observed live; kept for schema safety
    "pass_yd": "pass_yards",
    "pass_td": "pass_tds",
    "pass_int": "interceptions",
    "pass_int_td": "pick_sixes",
    "rush_att": "rush_attempts",
    "rush_yd": "rush_yards",
    "rush_td": "rush_tds",
    "rec": "receptions",
    "rec_yd": "rec_yards",
    "rec_td": "rec_tds",
    "fum": "fumbles",  # not observed live (only fum_lost/fum_rec are); kept defensively
    "fum_lost": "fumbles_lost",
    "pr_yd": "return_yards_punt",
    "kr_yd": "return_yards_kick",  # summed below; not observed live
    # kickers — confirmed live: only 40-49/50+ buckets are projected at season
    # level (0-19/20-29/30-39 buckets never appear in the live vocabulary).
    "fgm_40_49": "fg_40_49",
    "fgm_50p": "fg_50_plus",
    "xpm": "pat_made",
    "xpmiss": "pat_missed",
    # kept mapped defensively though not observed live (season-level snapshot
    # never carries sub-40 buckets); a real key of this name would map cleanly.
    "fgm_0_19": "fg_0_19",
    "fgm_20_29": "fg_20_29",
    "fgm_30_39": "fg_30_39",
    "fgmiss_0_19": "fg_miss_0_19",
    "fgmiss_20_29": "fg_miss_20_29",
    "fgmiss_30_39": "fg_miss_30_39",
    # pr_td (individual punt-return TD) and def_kr_td (kick-return TD) are
    # confirmed live on WR/RB *and* DEF-position records — they map
    # unambiguously to the existing return_tds field/weight, summed like
    # two-point conversions. This is a deliberate correction of the brief's
    # guessed "st_td" key, which does not exist in the real vocabulary.
    "pr_td": "return_tds_pr",
    "def_kr_td": "return_tds_kr",  # summed below
}
_TWO_PT_KEYS = ("pass_2pt", "rush_2pt", "rec_2pt")
_IGNORED_EXACT = {
    "gp",  # games played — metadata, not scored
    "cmp_pct",  # derived %, redundant with pass_cmp/pass_att
    "pass_att",  # not scored directly; used to DERIVE incompletions (att-cmp) below
    "pass_sack",  # not observed live (only bare 'sack' appears, DEF-only — see below)
    "rec_tgt",  # not observed live; targets are not scored
    "fgm",
    "fga",
    "xpa",  # not observed live; aggregate counts, not scored (buckets are)
    "fgm_yds",  # sum of yards on made FGs — no matching StatLine field, not scored
    "fgmiss_40_49",
    "fgmiss_50p",  # confirmed live (K-only); league only scores
    # misses through the 30-39 bucket (config/scoring/v1.json kicking.weights
    # has no 40-49/50+ miss entries and StatLine has no matching fields) —
    # deliberately unscored, not a mapping gap.
    "pass_fd",  # confirmed live, QB-only. Used only by the ingester's
    # per-position FD validation (SleeperProjectionsIngester._FD_BY_POSITION);
    # the league's FD bonus only scores rush/rec first downs (config has no
    # "pass_first_downs" weight and StatLine has no matching field) — QB
    # passing first downs are not an individually scored stat.
    # --- rush_fd / rec_fd: REJECTED as a scoring input (2026-07-09). Verified
    # against nflverse 2019-2025 ground truth (see fd_impute.py and
    # docs/research/2026-07-09-fd-imputation-divergence.md): Sleeper's native
    # FD projections run ~2x our fitted historical rates (RB rush-FD/carry
    # 0.18-0.27 true vs. 0.41-0.50 projected; RB rec-FD/rec 0.27-0.49 true vs.
    # 0.76-0.87 projected) and are frequently internally impossible — 53% of
    # rec pairs and 96% of pass pairs have native_fd > native_volume (more
    # first downs than the receptions/completions that could produce them).
    # Under this league's +1/FD scoring, that inflation is worth ~50-70
    # phantom points/season for volume players. DECISION: imputed FD from
    # ffi.scoring.fd_impute.impute_fd (fitted on real nflverse plays) is the
    # FD source for ALL projection scoring; these keys are kept mapped here
    # only as an ingest-shape check (still validated/classified as "known"),
    # never fed into the StatLine that gets scored.
    "rush_fd",
    "rec_fd",
    # --- Team-DST stats: confirmed live, DEF-position-only (verified via
    # psql cross-tab of position x key). Full DST scoring needs its own
    # dispatch (tier semantics for pts_allow_0 / yds_allow_0_100 are not
    # self-evident from the key names alone — unlike Yahoo's one-hot tier
    # indicators, Sleeper appears to give a single flat projected value,
    # unconfirmed against Sleeper's docs) — deliberately deferred rather than
    # guessed at (R5: a wrong guess would silently corrupt DST points; an
    # explicit, documented ignore does not). Follow-up: dedicated DST/IDP
    # mapping task, analogous to yahoo_adapter's DT tier-indicator handling.
    "sack",
    "int",
    "fum_rec",
    "blk_kick",
    "def_fum_td",
    "pts_allow_0",
    "yds_allow_0_100",
    # IDP (individual defensive player) stats — out of scope, same reasoning.
    "idp_fum_rec",
    "idp_int",
    "idp_tkl",
    "idp_tkl_ast",
    "idp_tkl_solo",
}
_IGNORED_PREFIXES = (
    "pts_",  # Sleeper's own fantasy-point projections (pts_ppr/std/half_ppr)
    # plus pts_allow_0 (DST tier input, deferred above) — both redundant with
    # our own scoring engine / out of scope.
    "adp_",
    "pos_adp_",  # ADP / draft-position metadata
    "bonus_",  # Sleeper's own PPR bonus categories, not in our league rules
    "rec_0_",
    "rec_5_",
    "rec_10_",
    "rec_20_",
    "rec_30_",
    "rec_40",  # reception-distance bonus buckets
    "rush_40",  # rush-distance bonus bucket
    "pass_cmp_40",  # completion-distance bonus bucket
    "idp_",  # individual defensive player stats — deferred (see _IGNORED_EXACT)
    "def_",  # team defense TD/turnover stats — deferred (see _IGNORED_EXACT);
    # also covers def_fum_td/def_kr_td by prefix, but def_kr_td is explicitly
    # mapped above (it appears on WR/RB records too, not just DEF) — the
    # mapping in _SLEEPER_MAP takes precedence over prefix classification.
)


def _classify(key: str) -> str | None:
    if key in _SLEEPER_MAP or key in _TWO_PT_KEYS or key in _IGNORED_EXACT:
        return "known"
    if any(key.startswith(p) for p in _IGNORED_PREFIXES):
        return "known"
    return None


def stat_line_from_sleeper(record: dict) -> StatLine:
    if "stats" not in record or not isinstance(record["stats"], dict):
        raise IngestError(f"sleeper record missing stats dict: {str(record)[:200]}")
    stats = record["stats"]
    unknown = [k for k in stats if _classify(k) is None]
    if unknown:
        raise IngestError(
            f"sleeper stats has unmapped keys {sorted(unknown)} — schema drift; "
            "map or ignore explicitly (never silently)"
        )
    fields: dict[str, float] = {}
    for k, f in _SLEEPER_MAP.items():
        if k in stats and f not in (
            "return_yards_punt",
            "return_yards_kick",
            "return_tds_pr",
            "return_tds_kr",
        ):
            fields[f] = float(stats[k])
    ret_yards = sum(float(stats[k]) for k in ("pr_yd", "kr_yd") if k in stats)
    if "pr_yd" in stats or "kr_yd" in stats:
        fields["return_yards"] = ret_yards
    ret_tds = sum(float(stats[k]) for k in ("pr_td", "def_kr_td") if k in stats)
    if "pr_td" in stats or "def_kr_td" in stats:
        fields["return_tds"] = ret_tds
    two = sum(float(stats[k]) for k in _TWO_PT_KEYS if k in stats)
    if any(k in stats for k in _TWO_PT_KEYS):
        fields["two_point_conversions"] = two
    # Incompletions are scored (-0.5, config/scoring/v1.json) but Sleeper never
    # emits `pass_inc` at the season level -- only `pass_att` (otherwise ignored)
    # and `pass_cmp`. Derive incompletions = att - cmp so the penalty our own
    # scoring config specifies is actually applied; without this every projected
    # QB is over-scored by 0.5*(att-cmp) (~50-100 pts) and the QB ranking is
    # distorted (the penalty scales with pass volume). Only when `pass_inc`
    # wasn't already mapped above (schema-safety path) and both att & cmp are
    # present. att < cmp is physically impossible -> fail loud (R5), never emit a
    # negative incompletion count (which would ADD points, wrong direction).
    if (
        "pass_incompletions" not in fields
        and "pass_att" in stats
        and "pass_cmp" in stats
    ):
        inc = float(stats["pass_att"]) - float(stats["pass_cmp"])
        if inc < 0:
            raise IngestError(
                f"sleeper pass_att ({stats['pass_att']}) < pass_cmp "
                f"({stats['pass_cmp']}) -- impossible; refusing to derive a "
                "negative incompletion count"
            )
        fields["pass_incompletions"] = inc
    return StatLine(**fields)
