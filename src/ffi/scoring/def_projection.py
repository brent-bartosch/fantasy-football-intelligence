"""Sleeper DEF (team-defense) season-projection scoring.

Verified live (scripts/verify_dst_semantics.py, docs/research/
2026-07-10-dst-semantics.md — Step 1/2): Sleeper's season-level snapshot
gives 4 real counting stats per team (sack, int, fum_rec, blk_kick). Their
weighted sum under Sleeper's own default weights (1, 2, 2, 2 — identical to
this league's cfg.defense.weights for the same fields) reconstructs
Sleeper's own `pts_std` EXACTLY (0% residual) for all 32/32 teams — far
exceeding the >=28/32 @5% reconstruction gate.

The bucket-shaped fields `pts_allow_0` and `yds_allow_0_100` are exactly
1.0 for every one of the 32 teams (zero variance — a shutout-caliber
defense and a historically bad one both report 1.0) and, per that same
exact reconstruction, contribute NOTHING to Sleeper's own scoring. They are
verified-junk season-snapshot placeholders, not real per-team
games-in-bucket counts (unlike Yahoo's weekly one-hot tier indicators,
ffi.scoring.yahoo_adapter._DEF_PTS_INDICATORS, which DO vary genuinely).
A handful of rare TD-adjacent keys (pass_int_td, def_fum_td, pr_td,
def_kr_td — nonzero for only 1-5/32 teams) are also verified zero-weight
the same way. All of the above are treated as ignored metadata below —
NOT fed through the tier machinery — precisely because feeding a constant,
non-discriminating placeholder through _tier_points would inject a false
"every team gets credit for a shutout game" bonus that Sleeper itself does
not award.

The bucket-tier mechanism (pts_allow_1_6, pts_allow_7_13, ...,
yds_allow_300_349, ...) IS wired in generically against
cfg.defense.points_allowed_tiers / yards_allowed_tiers, for schema
robustness if Sleeper ever starts emitting genuine per-tier game counts.
It never fires against the current live season snapshot, since only the
two verified-junk single-bucket names above ever appear there.

Sleeper does not project this league's enhanced DEF categories at all
(defensive_tds, safeties, fourth_down_stops, tackles_for_loss,
three_and_outs, extra_point_returns — no matching keys ever observed live)
NOR does it give usable points_allowed/yards_allowed signal (see above) —
`fit_def_uplift` fits one flat per-week constant, from 2025 ground truth,
covering ALL of those categories together (a deliberate widening of the
brief's original zero-out list — see the research doc's "deviation" note).
`def_projection_points` adds `uplift_per_week * games` to cover them."""
import re
from decimal import Decimal

from ffi.scoring.config import ScoringConfig
from ffi.scoring.engine import _d, _tier_points
from ffi.scoring.yahoo_adapter import stat_line_from_yahoo
from ffi.scoring.engine import score_components

_COUNTING_MAP = {
    "sack": "sacks",
    "int": "def_interceptions",
    "fum_rec": "fumble_recoveries",
    "blk_kick": "blocked_kicks",
}

# Verified-junk / metadata keys: proven (by exact pts_std reconstruction,
# scripts/verify_dst_semantics.py Step 2) to contribute ZERO to Sleeper's
# own scoring. Listed explicitly (not silently dropped by a catch-all) so
# the fail-loud guarantee on genuinely new/unmapped keys still holds.
_IGNORED_EXACT = {
    "gp",  # always 1.0 for DEF records — not a real games-played count
    "pts_allow_0",  # always 1.0 for all 32 teams; verified junk placeholder
    "yds_allow_0_100",  # ditto
    "pass_int_td",
    "def_fum_td",
    "pr_td",
    "def_kr_td",  # rare TD-adjacent keys (nonzero for 1-5/32 teams); verified zero-weight
    "pts_ppr",
    "pts_std",
    "pts_half_ppr",  # Sleeper's own point projections — informational only
}
_IGNORED_PREFIXES = ("adp_", "pos_adp_")

_PTS_BUCKET_PLUS_RE = re.compile(r"^pts_allow_(\d+)p$")
_PTS_BUCKET_RE = re.compile(r"^pts_allow_(\d+)(?:_(\d+))?$")
_YDS_BUCKET_PLUS_RE = re.compile(r"^yds_allow_(\d+)p$")
_YDS_BUCKET_NEG_RE = re.compile(r"^yds_allow_neg$")
_YDS_BUCKET_RE = re.compile(r"^yds_allow_(\d+)_(\d+)$")


def _bucket(key: str) -> tuple[str, float] | None:
    """(family, representative value) for a canonical bucket key name, or
    None if the key doesn't match the bucket-naming convention at all.
    Representative value = the bucket's lower bound (deterministic;
    matches `cfg.defense.*_tiers`' contiguous max-based ranges for every
    canonical bucket boundary the league config actually defines)."""
    m = _PTS_BUCKET_PLUS_RE.match(key)
    if m:
        return "pts_allow", float(m.group(1))
    m = _PTS_BUCKET_RE.match(key)
    if m:
        return "pts_allow", float(m.group(1))
    m = _YDS_BUCKET_PLUS_RE.match(key)
    if m:
        return "yds_allow", float(m.group(1))
    if _YDS_BUCKET_NEG_RE.match(key):
        return "yds_allow", -1.0
    m = _YDS_BUCKET_RE.match(key)
    if m:
        return "yds_allow", float(m.group(1))
    return None


def def_projection_points(
    stats: dict, cfg: ScoringConfig, uplift_per_week: float, games: float = 17.0
) -> tuple[float, dict]:
    counting = Decimal("0")
    pts_allow_tiers = Decimal("0")
    yds_allow_tiers = Decimal("0")
    for key, value in stats.items():
        if key in _IGNORED_EXACT or any(key.startswith(p) for p in _IGNORED_PREFIXES):
            continue
        if key in _COUNTING_MAP:
            field = _COUNTING_MAP[key]
            weight = cfg.defense.weights[field]  # KeyError = config drift: fail loud
            counting += _d(float(value)) * _d(weight)
            continue
        bucketed = _bucket(key)
        if bucketed is not None:
            family, lower = bucketed
            tiers = (
                cfg.defense.points_allowed_tiers
                if family == "pts_allow"
                else cfg.defense.yards_allowed_tiers
            )
            contribution = _d(float(value)) * _tier_points(lower, tiers)
            if family == "pts_allow":
                pts_allow_tiers += contribution
            else:
                yds_allow_tiers += contribution
            continue
        raise ValueError(f"unmapped DEF stat key: {key!r}")

    comps = {
        "counting": counting,
        "pts_allow_tiers": pts_allow_tiers,
        "yds_allow_tiers": yds_allow_tiers,
        "uplift": _d(uplift_per_week) * _d(games),
    }
    points = sum(comps.values(), Decimal("0"))
    return float(points), comps


# Sleeper-covered counting fields (verified: their weighted sum reconstructs
# Sleeper's own pts_std exactly). Everything else this league scores for DEF
# — defensive_tds, safeties, points_allowed, yards_allowed tiers, and the
# genuinely-uncovered fourth_down_stops/tackles_for_loss/three_and_outs/
# extra_point_returns — gets folded into the single fitted uplift constant,
# because Sleeper's season snapshot gives us NO usable signal for any of
# them (see module docstring). This deliberately widens the brief's
# original zero-out list (which additionally zeroed defensive_tds,
# safeties, points_allowed, yards_allowed) — that list assumed Sleeper
# would supply genuine points_allowed/yards_allowed bucket data, which the
# live verification disproved.
_UPLIFT_ZERO_FIELDS = (
    "sacks",
    "def_interceptions",
    "fumble_recoveries",
    "blocked_kicks",
)


def fit_def_uplift(conn, cfg: ScoringConfig, season: int = 2025) -> float:
    """League-mean weekly points from DEF scoring categories Sleeper's
    season snapshot gives us no usable signal for, fitted on `season`'s
    real yahoo_engine-scored team-defense weeks (raw.yahoo_player_week,
    position_type='DT', via the existing yahoo_adapter — reused per the
    brief rather than re-deriving a second DEF stat-line builder)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stats FROM raw.yahoo_player_week "
            "WHERE season=%s AND stats->>'position_type'='DT'",
            (season,),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError(
            f"no season={season} DEF (position_type='DT') weekly rows in "
            "raw.yahoo_player_week — cannot fit the DEF uplift"
        )
    total = Decimal("0")
    for (stats,) in rows:
        line = stat_line_from_yahoo(stats)
        line = line.model_copy(update={f: 0.0 for f in _UPLIFT_ZERO_FIELDS})
        total += sum(score_components(line, cfg).values(), Decimal("0"))
    # Round to 4dp before returning as float — matches the repo convention
    # for statistically-fitted (not exact-scoring) quantities feeding back
    # into the Decimal-exact engine (see projection_bonus.season_bonus_ev's
    # round(...,4) before Decimal conversion) — avoids surfacing raw binary
    # float division noise (e.g. "164.2187500000000120") in stored components.
    return float(round(total / len(rows), 4))
