"""Replacement baselines COMPUTED from league shape (design 4.3, R16).

League shape (league_rules.md): 12 teams; starters QB2 / RB2 / WR3 / TE1 /
FLEX1 (W/R/T only — QB not flex-eligible) / K1 / DEF1.
FLEX allocation: split across RB/WR/TE in proportion to historical flex usage;
default 0.5/0.4/0.1 (parameterized in the scenario — sensitivity report varies it).
QB hoarding: 2QB leagues roster QBs beyond starters; scenario adds
qb_extra_rostered to QB demand (0 = pure starters, 12 = one bench QB per team,
24 = two)."""

STARTERS = {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}
FLEX_SLOTS = 1
DEFAULT_FLEX_SHARE = {"RB": 0.5, "WR": 0.4, "TE": 0.1}


def compute_replacement_ranks(scenario: dict) -> dict[str, int]:
    teams = scenario["teams"]
    flex_share = scenario.get("flex_share", DEFAULT_FLEX_SHARE)
    if abs(sum(flex_share.values()) - 1.0) > 1e-9:
        raise ValueError(f"flex_share must sum to 1: {flex_share}")
    ranks = {}
    for pos, n in STARTERS.items():
        demand = teams * n
        if pos in flex_share:
            demand += round(teams * FLEX_SLOTS * flex_share[pos])
        if pos == "QB":
            demand += scenario.get("qb_extra_rostered", 0)
        ranks[pos] = int(demand)
    return ranks


def compute_baselines(
    points_by_pos: dict[str, list[float]], replacement_ranks: dict[str, int]
) -> dict[str, float]:
    """points_by_pos values must be sorted descending. Replacement points =
    the Nth-best player's points (N = replacement rank)."""
    out = {}
    for pos, rank in replacement_ranks.items():
        pool = points_by_pos.get(pos)
        if pool is None:
            raise ValueError(f"no projection pool for position {pos}")
        if sorted(pool, reverse=True) != list(pool):
            raise ValueError(f"points for {pos} must be sorted descending")
        if len(pool) < rank:
            raise ValueError(
                f"{pos}: fewer players projected ({len(pool)}) than replacement rank {rank}"
            )
        out[pos] = pool[rank - 1]
    return out
