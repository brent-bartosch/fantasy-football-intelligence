"""First-down imputation (R16): rates fitted on nflverse 2019-2025, applied to
projected volume for FD-less sources (FantasyPros).

Method (documented for the divergence report):
- position rate = sum(FD) / sum(volume) per position, all seasons pooled
- player rate  = empirical-Bayes shrunk: (player_fd + k*pos_rate) / (player_vol + k)
  with prior strength k = 50 carries / 30 receptions / 100 completions
- imputation uses the player rate when the player has history, else position rate.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FdRates:
    position_rates: dict  # pos -> {"rush_fd_per_carry": r, "rec_fd_per_rec": r, ...}
    player_rates: dict  # gsis_id -> subset of the same keys (already shrunk)
    prior_strength: dict = field(
        default_factory=lambda: {"rush": 50.0, "rec": 30.0, "pass": 100.0}
    )


_KINDS = [  # (rate key, fd column, volume column, prior key)
    ("rush_fd_per_carry", "rushing_first_downs", "carries", "rush"),
    ("rec_fd_per_rec", "receiving_first_downs", "receptions", "rec"),
    ("pass_fd_per_cmp", "passing_first_downs", "completions", "pass"),
]


def fit_fd_rates(conn, seasons: list[int]) -> FdRates:
    position_rates: dict = {}
    player_rates: dict = {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT position,
                      sum(rushing_first_downs)::float, sum(carries)::float,
                      sum(receiving_first_downs)::float, sum(receptions)::float,
                      sum(passing_first_downs)::float, sum(completions)::float
               FROM raw.nflverse_player_week
               WHERE season = ANY(%s) AND position IN ('QB','RB','WR','TE')
               GROUP BY position""",
            (seasons,),
        )
        for pos, rfd, car, cfd, rec, pfd, cmp_ in cur.fetchall():
            rates = {}
            if car and car > 0:
                rates["rush_fd_per_carry"] = rfd / car
            if rec and rec > 0:
                rates["rec_fd_per_rec"] = cfd / rec
            if cmp_ and cmp_ > 0:
                rates["pass_fd_per_cmp"] = pfd / cmp_
            position_rates[pos] = rates
        prior = {"rush": 50.0, "rec": 30.0, "pass": 100.0}
        cur.execute(
            """SELECT gsis_id, max(position),
                      sum(rushing_first_downs)::float, sum(carries)::float,
                      sum(receiving_first_downs)::float, sum(receptions)::float,
                      sum(passing_first_downs)::float, sum(completions)::float
               FROM raw.nflverse_player_week
               WHERE season = ANY(%s) AND position IN ('QB','RB','WR','TE')
               GROUP BY gsis_id""",
            (seasons,),
        )
        for gsis, pos, rfd, car, cfd, rec, pfd, cmp_ in cur.fetchall():
            pos_r = position_rates.get(pos)
            if not pos_r:
                continue
            triples = [
                ("rush_fd_per_carry", rfd, car, "rush"),
                ("rec_fd_per_rec", cfd, rec, "rec"),
                ("pass_fd_per_cmp", pfd, cmp_, "pass"),
            ]
            shrunk = {}
            for key, fd, vol, pk in triples:
                if key in pos_r and vol and vol > 0:
                    k = prior[pk]
                    shrunk[key] = (fd + k * pos_r[key]) / (vol + k)
            if shrunk:
                player_rates[gsis] = shrunk
    return FdRates(position_rates=position_rates, player_rates=player_rates)


def impute_fd(
    rates: FdRates,
    position: str,
    gsis_id: str | None,
    carries: float,
    receptions: float,
    completions: float,
) -> dict:
    pos_r = rates.position_rates[position]  # KeyError = unknown position: fail loud
    ply_r = rates.player_rates.get(gsis_id, {}) if gsis_id else {}

    def rate(key):
        if key in ply_r:
            return ply_r[key]
        return pos_r[key]  # KeyError if the position genuinely lacks the rate: loud

    out = {"rush_first_downs": 0.0, "rec_first_downs": 0.0, "pass_first_downs": 0.0}
    if carries:
        out["rush_first_downs"] = carries * rate("rush_fd_per_carry")
    if receptions:
        out["rec_first_downs"] = receptions * rate("rec_fd_per_rec")
    if completions:
        out["pass_first_downs"] = completions * rate("pass_fd_per_cmp")
    return out
