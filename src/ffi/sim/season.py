"""Season evaluator (Phase 3 / Task 9): gamma weekly draws -> optimal lineups
-> all-play win rates. Turns `run_draft`'s rosters (Task 7's
`DraftResult.rosters`) into the shared metric the sim farm (Task 12) and
backtests (Task 11) both consume. Vectorized (numpy) throughout -- the farm
runs ~13k drafts x 20 seasons x 14 weeks nightly, so this has to be fast.

Two modes:
  - Monte Carlo (`points_lookup=None`): per player `mean_w = proj_points/17`;
    weekly points ~ Gamma(k=1/cv^2, theta=mean_w*cv^2) using the player's
    POSITION cv (from `fit_weekly_points_cv`); one bye week per player per
    season, uniform in `BYE_WINDOW`, zeroes that week. Fully vectorized
    (seasons, weeks, players) draws off a single `np.random.default_rng(seed)`.
  - `points_lookup` mode (Task 11 backtests): weekly points =
    `points_lookup.get((gsis_id, week), 0.0)`, deterministic, `n_seasons`
    ignored (single pass). Players with `gsis_id=None` (e.g. DEF, in the
    backtest's own representation) ALWAYS score 0.0 in this mode -- this is a
    hardcoded branch, not merely reliance on the caller never putting a
    `(None, week)` key in `points_lookup`. Task 11 owns any DEF-neutralization
    logic upstream of this contract; this module implements exactly the
    contract above and nothing cleverer.

Lineup per team-week: top 2 QB + 2 RB + 3 WR + 1 TE + 1 K + 1 DEF by that
week's points, plus the single best remaining RB/WR/TE as FLEX. All-play:
each week, a team "beats" every other team in the league with a strictly
lower total that week (ties count for neither side, matching
`ffi.history.mining.all_play_from_weeks`'s convention); pct = wins /
((teams-1) * weeks [* seasons]).

Perf: the only Python-level loop is over teams (12) for lineup assembly --
every (season, week) slice for a team is handled by one vectorized sort per
position (see `_lineup_total`), per the brief."""
from collections import defaultdict

import numpy as np

from ffi.scoring.config import load_config_v1
from ffi.sim.pool import PoolPlayer

REG_WEEKS = 14
BYE_WINDOW = (5, 14)  # inclusive week range a player's single bye can fall in

STARTERS = {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}
FLEX_POS = ("RB", "WR", "TE")

_CV_SEASON_LO, _CV_SEASON_HI = 2019, 2025
_ACTIVE_STAT_POSITIONS = ("QB", "RB", "WR", "TE")


def fit_weekly_points_cv(conn) -> dict[str, float]:
    """{'QB','RB','WR','TE','K','DEF'} -> weekly LEAGUE-SCORED points CV
    (sd/mean). QB/RB/WR/TE/K pooled over ACTIVE weeks, 2019-2025; DEF is the
    mean of each of the 32 teams' own 2025 sd/mean (see below -- a different
    pooling rule, per the brief). Fails loud (ValueError naming the position)
    if any of the six can't be fitted -- a silently-missing position would
    make every downstream Gamma draw for it look plausible while drawing
    from an undefined distribution."""
    config_version = load_config_v1().version
    cv: dict[str, float] = {}
    with conn.cursor() as cur:
        # QB/RB/WR/TE: scoring.player_week_points(source='nflverse') joined to
        # raw.nflverse_player_week on (gsis, season, week); active weeks only
        # (any of carries/receptions/completions/attempts > 0). Pooled
        # sd/mean per position (NOT an average of per-player CVs).
        cur.execute(
            """
            SELECT n.position, avg(p.points), stddev_samp(p.points)
            FROM scoring.player_week_points p
            JOIN raw.nflverse_player_week n
              ON n.gsis_id = p.player_ref AND n.season = p.season AND n.week = p.week
            WHERE p.source = 'nflverse' AND p.config_version = %s
              AND p.season BETWEEN %s AND %s
              AND n.position = ANY(%s)
              AND (coalesce(n.carries, 0) > 0 OR coalesce(n.receptions, 0) > 0
                   OR coalesce(n.completions, 0) > 0 OR coalesce(n.attempts, 0) > 0)
            GROUP BY n.position
            """,
            (
                config_version,
                _CV_SEASON_LO,
                _CV_SEASON_HI,
                list(_ACTIVE_STAT_POSITIONS),
            ),
        )
        for pos, mean, sd in cur.fetchall():
            if mean is not None and sd is not None and float(mean) > 0:
                cv[pos] = float(sd) / float(mean)
        for pos in _ACTIVE_STAT_POSITIONS:
            if pos not in cv:
                raise ValueError(
                    f"fit_weekly_points_cv: no active nflverse player-weeks found "
                    f"for position {pos!r} ({_CV_SEASON_LO}-{_CV_SEASON_HI}) -- is "
                    "raw.nflverse_player_week loaded and joined correctly?"
                )

        # K: same join. Verified live (2026-07-10): raw.nflverse_player_week.
        # position uses 'K' (never 'PK') for kickers -- 3,914 'K' rows, zero
        # 'PK' rows in the live DB. Active = points <> 0 (kickers carry no
        # carries/receptions/completions/attempts volume stat to key off).
        cur.execute(
            """
            SELECT avg(p.points), stddev_samp(p.points)
            FROM scoring.player_week_points p
            JOIN raw.nflverse_player_week n
              ON n.gsis_id = p.player_ref AND n.season = p.season AND n.week = p.week
            WHERE p.source = 'nflverse' AND p.config_version = %s
              AND p.season BETWEEN %s AND %s
              AND n.position = 'K' AND p.points <> 0
            """,
            (config_version, _CV_SEASON_LO, _CV_SEASON_HI),
        )
        row = cur.fetchone()
        if (
            row is not None
            and row[0] is not None
            and row[1] is not None
            and float(row[0]) > 0
        ):
            cv["K"] = float(row[1]) / float(row[0])
        else:
            raise ValueError(
                "fit_weekly_points_cv: no active (points<>0) nflverse K "
                f"player-weeks found ({_CV_SEASON_LO}-{_CV_SEASON_HI})"
            )

        # DEF: yahoo_engine 2025 weekly DEF points via team_def_map
        # (player_ref = yahoo_def_id -- the join itself is the position
        # filter, since only the 32 DEF ids match). cv = MEAN OF PER-TEAM
        # sd/mean (not pooled across all 32 teams' weeks) -- per the brief.
        cur.execute(
            """
            SELECT d.team_abbr, avg(p.points), stddev_samp(p.points)
            FROM scoring.player_week_points p
            JOIN public.team_def_map d ON d.yahoo_def_id = p.player_ref
            WHERE p.source = 'yahoo_engine' AND p.config_version = %s AND p.season = 2025
            GROUP BY d.team_abbr
            """,
            (config_version,),
        )
        def_rows = cur.fetchall()

    per_team_cv = [
        float(sd) / float(mean)
        for _, mean, sd in def_rows
        if mean is not None and sd is not None and float(mean) > 0
    ]
    if not per_team_cv:
        raise ValueError(
            "fit_weekly_points_cv: no position 'DEF' cv computable -- no "
            "yahoo_engine 2025 DEF weeks found via public.team_def_map "
            "(expected 32 teams)"
        )
    cv["DEF"] = sum(per_team_cv) / len(per_team_cv)
    return cv


def _build_index(
    rosters: dict[int, list[PoolPlayer]],
) -> tuple[list[int], list[PoolPlayer], dict[int, dict[str, list[int]]]]:
    """Flatten all rosters into one global player list (stable order) plus a
    team -> position -> [global index] map, so every team's lineup slice can
    be pulled straight out of one shared (seasons, weeks, players) array."""
    team_keys = sorted(rosters.keys())
    players_flat: list[PoolPlayer] = []
    team_pos_idx: dict[int, dict[str, list[int]]] = {}
    for t in team_keys:
        by_pos: dict[str, list[int]] = defaultdict(list)
        for p in rosters[t]:
            by_pos[p.position].append(len(players_flat))
            players_flat.append(p)
        team_pos_idx[t] = by_pos
    return team_keys, players_flat, team_pos_idx


def _lineup_total(points: np.ndarray, pos_idx: dict[str, list[int]]) -> np.ndarray:
    """points: (..., P) -- trailing axis = players on ONE team's roster
    (indices per `pos_idx` point into whatever P-axis `points` has, e.g. the
    full league-wide player axis). Returns (...,), the optimal lineup total:
    top STARTERS-many players by points per position, plus the single best
    leftover RB/WR/TE as FLEX. Vectorized over arbitrary leading
    (season, week, ...) shape -- no Python loop over seasons/weeks."""
    total = np.zeros(points.shape[:-1])
    leftovers = []
    for pos, need in STARTERS.items():
        idxs = pos_idx.get(pos)
        if not idxs:
            continue
        arr = points[..., idxs]
        sorted_desc = -np.sort(-arr, axis=-1)
        n_take = min(need, sorted_desc.shape[-1])
        total = total + sorted_desc[..., :n_take].sum(axis=-1)
        if pos in FLEX_POS and sorted_desc.shape[-1] > n_take:
            leftovers.append(sorted_desc[..., n_take:])
    if leftovers:
        combined = np.concatenate(leftovers, axis=-1)
        total = total + combined.max(axis=-1)
    return total


def _mc_weekly_points(
    players_flat: list[PoolPlayer],
    cv_by_pos: dict[str, float],
    seed: int,
    n_seasons: int,
) -> np.ndarray:
    """(n_seasons, REG_WEEKS, len(players_flat)) Gamma-drawn weekly points;
    one bye week per player per season zeroed (uniform in BYE_WINDOW). A
    single `np.random.default_rng(seed)` drives both the point draws and the
    bye-week draws, so the same seed reproduces byte-identical output."""
    n_players = len(players_flat)
    rng = np.random.default_rng(seed)
    try:
        cv_arr = np.array([cv_by_pos[p.position] for p in players_flat], dtype=float)
    except KeyError as e:
        raise ValueError(f"evaluate_league: cv_by_pos missing position {e}") from e
    mean_w = np.array([p.proj_points / 17.0 for p in players_flat], dtype=float)
    k_arr = 1.0 / (cv_arr**2)
    theta_arr = mean_w * (cv_arr**2)
    points = (
        rng.standard_gamma(k_arr, size=(n_seasons, REG_WEEKS, n_players)) * theta_arr
    )
    bye_week = rng.integers(
        BYE_WINDOW[0], BYE_WINDOW[1] + 1, size=(n_seasons, n_players)
    )
    s_idx = np.arange(n_seasons)[:, None]
    p_idx = np.arange(n_players)[None, :]
    points[s_idx, bye_week - 1, p_idx] = 0.0
    return points


def _lookup_weekly_points(
    players_flat: list[PoolPlayer], points_lookup: dict[tuple[str, int], float]
) -> np.ndarray:
    """(1, REG_WEEKS, len(players_flat)) deterministic points from
    `points_lookup.get((gsis_id, week), 0.0)`. `gsis_id=None` ALWAYS scores
    0.0 (hardcoded -- see module docstring), regardless of `points_lookup`'s
    contents."""
    n_players = len(players_flat)
    points = np.zeros((1, REG_WEEKS, n_players))
    for j, p in enumerate(players_flat):
        if p.gsis_id is None:
            continue
        for w in range(1, REG_WEEKS + 1):
            points[0, w - 1, j] = points_lookup.get((p.gsis_id, w), 0.0)
    return points


def evaluate_league(
    rosters: dict[int, list[PoolPlayer]],
    cv_by_pos: dict[str, float],
    seed: int,
    n_seasons: int = 20,
    points_lookup: dict[tuple[str, int], float] | None = None,
) -> dict[int, float]:
    """draft position -> mean all-play win pct. See module docstring for the
    two modes' semantics."""
    team_keys, players_flat, team_pos_idx = _build_index(rosters)
    n_teams = len(team_keys)

    if points_lookup is not None:
        points = _lookup_weekly_points(players_flat, points_lookup)
        s_eff = 1
    else:
        points = _mc_weekly_points(players_flat, cv_by_pos, seed, n_seasons)
        s_eff = n_seasons

    totals = np.zeros((n_teams, s_eff, REG_WEEKS))
    for ti, t in enumerate(team_keys):
        totals[ti] = _lineup_total(points, team_pos_idx[t])

    # All-play: for every (season, week), each team beats every OTHER team
    # with a strictly lower total that slot. Broadcasting over the small
    # (teams x teams x seasons x weeks) grid is cheaper and simpler than a
    # Python-level opponent loop, and this whole comparison is tiny (12x12
    # booleans x seasons x weeks).
    gt = totals[:, None, :, :] > totals[None, :, :, :]
    wins_per_slot = gt.sum(axis=1)  # (teams, seasons, weeks)
    total_wins = wins_per_slot.sum(axis=(1, 2))  # (teams,)
    denom = (n_teams - 1) * s_eff * REG_WEEKS
    pct = total_wins / denom
    return {team_keys[i]: float(pct[i]) for i in range(n_teams)}
