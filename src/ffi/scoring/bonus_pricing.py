"""Distribution-based threshold-bonus pricing (R16).

Weekly yardage Y ~ Gamma(shape k, scale theta) with k = 1/cv^2,
theta = mean * cv^2 (so E[Y]=mean, SD/mean=cv). Gamma: positive support,
right-skewed — matches weekly yardage shape far better than normal.
CV per player from weekly history (active weeks only), position fallback
for thin histories. CALIBRATION protocol fits CV on 2019-2022 (out-of-sample
vs. 2023-25 eval, see calibration report); production use may fit on all
available seasons via the `seasons` arg."""
from scipy.stats import gamma as gamma_dist

from ffi.scoring.config import BonusTier


def weekly_threshold_prob(mean_weekly: float, cv: float, threshold: float) -> float:
    if cv <= 0:
        raise ValueError(f"cv must be positive, got {cv}")
    if mean_weekly <= 0:
        return 0.0
    k = 1.0 / (cv * cv)
    theta = mean_weekly * cv * cv
    return float(gamma_dist.sf(threshold, a=k, scale=theta))


def bonus_ev_per_week(mean_weekly: float, cv: float, tiers: list[BonusTier]) -> float:
    if cv <= 0:
        raise ValueError(f"cv must be positive, got {cv}")
    if mean_weekly <= 0:
        return 0.0
    return sum(
        weekly_threshold_prob(mean_weekly, cv, t.threshold) * t.points for t in tiers
    )


_STAT_COLS = {
    "rush_yards": "rushing_yards",
    "rec_yards": "receiving_yards",
    "pass_yards": "passing_yards",
}


def estimate_weekly_cv(conn, seasons: list[int], min_weeks: int = 8) -> dict:
    """Per-player weekly-yardage CV (sd/mean over ACTIVE weeks: volume > 0),
    plus per-position pooled fallback. Returns
    {"players": {gsis: {stat: cv}}, "positions": {pos: {stat: cv}}}."""
    out = {"players": {}, "positions": {}}
    with conn.cursor() as cur:
        for stat, col in _STAT_COLS.items():
            cur.execute(
                f"""SELECT gsis_id, max(position), avg({col}), stddev_samp({col}), count(*)
                    FROM raw.nflverse_player_week
                    WHERE season = ANY(%s) AND {col} > 0
                    GROUP BY gsis_id HAVING count(*) >= %s AND avg({col}) > 0""",
                (seasons, min_weeks),
            )
            for gsis, pos, mean, sd, _n in cur.fetchall():
                if sd is None or mean is None or float(mean) <= 0:
                    continue
                out["players"].setdefault(gsis, {})[stat] = float(sd) / float(mean)
            cur.execute(
                f"""SELECT position, avg({col}), stddev_samp({col})
                    FROM raw.nflverse_player_week
                    WHERE season = ANY(%s) AND {col} > 0 AND position IN ('QB','RB','WR','TE')
                    GROUP BY position HAVING avg({col}) > 0""",
                (seasons,),
            )
            for pos, mean, sd in cur.fetchall():
                out["positions"].setdefault(pos, {})[stat] = float(sd) / float(mean)
    if not out["positions"]:
        raise ValueError(
            "no position CVs computed — is raw.nflverse_player_week loaded?"
        )
    return out
