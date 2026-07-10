"""raw.yahoo_player_week.stats (display-name keys) -> StatLine.
Dispatch on position_type: 'O' offense, 'K' kicker, 'DT' team defense.
Unknown keys are schema drift and fail loud (ADR Domain 1)."""
from ffi.ingest.base import IngestError
from ffi.scoring.statline import StatLine

_META_KEYS = {"name", "player_id", "total_points", "position_type"}

_OFFENSE_MAP = {
    "Comp": "pass_completions",
    "Inc": "pass_incompletions",
    "Pass Yds": "pass_yards",
    "Pass TD": "pass_tds",
    "Int": "interceptions",
    "Pick Six": "pick_sixes",
    "Rush Att": "rush_attempts",
    "Rush Yds": "rush_yards",
    "Rush TD": "rush_tds",
    "Rush 1st Downs": "rush_first_downs",
    "Rec": "receptions",
    "Rec Yds": "rec_yards",
    "Rec TD": "rec_tds",
    "Rec 1st Downs": "rec_first_downs",
    "Ret Yds": "return_yards",
    "Ret TD": "return_tds",
    "2-PT": "two_point_conversions",
    "Fum": "fumbles",
    "Fum Lost": "fumbles_lost",
    "Fum Ret TD": "offensive_fumble_return_tds",
}
_OFFENSE_IGNORED = {"Targets"}  # informational; not scored

_K_MAP = {
    "FG 0-19": "fg_0_19",
    "FG 20-29": "fg_20_29",
    "FG 30-39": "fg_30_39",
    "FG 40-49": "fg_40_49",
    "FG 50+": "fg_50_plus",
    "FGM 0-19": "fg_miss_0_19",
    "FGM 20-29": "fg_miss_20_29",
    "FGM 30-39": "fg_miss_30_39",
    "PAT Made": "pat_made",
    "PAT Miss": "pat_missed",
}

_DEF_MAP = {
    "Sack": "sacks",
    "Int": "def_interceptions",
    "Fum Rec": "fumble_recoveries",
    "TD": "defensive_tds",
    "Safe": "safeties",
    "Blk Kick": "blocked_kicks",
    "4 Dwn Stops": "fourth_down_stops",
    "TFL": "tackles_for_loss",
    "3 and Outs": "three_and_outs",
    "XPR": "extra_point_returns",
    "Pts Allow": "points_allowed",
    "Def Yds Allow": "yards_allowed",
}
# One-hot tier indicators: not mapped (engine computes tiers from raw values)
# but cross-checked below — a free consistency test on every DEF row.
_DEF_PTS_INDICATORS = {
    "Pts Allow 0": (None, 0),
    "Pts Allow 1-6": (1, 6),
    "Pts Allow 7-13": (7, 13),
    "Pts Allow 14-20": (14, 20),
    "Pts Allow 21-27": (21, 27),
    "Pts Allow 28-34": (28, 34),
    "Pts Allow 35+": (35, None),
}
_DEF_YDS_INDICATORS = {
    "Yds Allow Neg": (None, -1),
    "Yds Allow 0-99": (0, 99),
    "Yds Allow 100-199": (100, 199),
    "Yds Allow 200-299": (200, 299),
    "Yds Allow 300-399": (300, 399),
    "Yds Allow 400-499": (400, 499),
    "Yds Allow 500+": (500, None),
}

_DISPATCH = {
    "O": (_OFFENSE_MAP, _OFFENSE_IGNORED),
    "K": (_K_MAP, set()),
    "DT": (_DEF_MAP, set(_DEF_PTS_INDICATORS) | set(_DEF_YDS_INDICATORS)),
}


def _check_indicators(stats: dict, raw_key: str, indicators: dict) -> None:
    value = stats[raw_key]
    for ind_key, (lo, hi) in indicators.items():
        if ind_key not in stats:
            continue
        expected = (
            1.0
            if ((lo is None or value >= lo) and (hi is None or value <= hi))
            else 0.0
        )
        if float(stats[ind_key]) != expected:
            raise IngestError(
                f"DEF tier indicator mismatch: {raw_key}={value} but "
                f"{ind_key}={stats[ind_key]} (expected {expected}) — payload inconsistent"
            )


def _tier_fired(stats: dict, indicators: dict) -> bool:
    return any(float(stats[k]) != 0.0 for k in indicators if k in stats)


def stat_line_from_yahoo(stats: dict) -> StatLine:
    if "position_type" not in stats:
        raise IngestError(
            f"yahoo stats payload missing position_type: {sorted(stats)[:20]}"
        )
    ptype = stats["position_type"]
    if ptype not in _DISPATCH:
        raise IngestError(
            f"unknown position_type {ptype!r} — extend the adapter deliberately"
        )
    key_map, ignored = _DISPATCH[ptype]
    unknown = set(stats) - set(key_map) - ignored - _META_KEYS
    if unknown:
        raise IngestError(
            f"yahoo stats payload has unmapped keys {sorted(unknown)} for "
            f"position_type={ptype} — schema drift; map or ignore explicitly"
        )
    fields = {f: float(stats[k]) for k, f in key_map.items() if k in stats}
    if ptype == "DT":
        # No tier indicator lit at all (all zero) means the team did not play
        # that week (bye/no game) — Yahoo scores no points-allowed/yards-allowed
        # tier bonus in that case (verified: total_points=0.00 for all such rows
        # in the 2025 sweep), so the field is genuinely absent, not an observed
        # zero. Skip the cross-check too since there is no tier to validate.
        if _tier_fired(stats, _DEF_PTS_INDICATORS):
            _check_indicators(stats, "Pts Allow", _DEF_PTS_INDICATORS)
        else:
            fields.pop("points_allowed", None)
        if _tier_fired(stats, _DEF_YDS_INDICATORS):
            _check_indicators(stats, "Def Yds Allow", _DEF_YDS_INDICATORS)
        else:
            fields.pop("yards_allowed", None)
    return StatLine(**fields)
