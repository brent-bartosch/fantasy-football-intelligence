"""Pure scoring core: (StatLine, ScoringConfig) -> Decimal. No I/O, no state,
no clock. Decimal-from-str arithmetic so results are exact (golden gate is
exact-match vs Yahoo)."""
from decimal import Decimal

from ffi.scoring.config import RangeTier, ScoringConfig
from ffi.scoring.statline import StatLine


def _d(x: float) -> Decimal:
    # float -> shortest-repr string -> Decimal: 0.33 becomes Decimal('0.33').
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(x)


def _tier_points(value: float, tiers: list[RangeTier]) -> Decimal:
    for t in tiers:
        if t.max is None or value <= t.max:
            return _d(t.points)
    raise ValueError(
        f"no tier matched value {value} — config tiers must end with max=null"
    )


def score_components(line: StatLine, cfg: ScoringConfig) -> dict[str, Decimal]:
    d = line.model_dump()
    comps: dict[str, Decimal] = {}

    weighted = Decimal("0")
    for section in (cfg.offense.weights, cfg.kicking.weights, cfg.defense.weights):
        for field, weight in section.items():
            v = d[field]  # KeyError = config names a stat StatLine lacks: fail loud
            if v is not None:
                weighted += _d(v) * _d(weight)
    comps["weights"] = weighted

    bonuses = Decimal("0")
    for field, tiers in cfg.offense.yardage_bonuses.items():
        v = d[field]
        if v is not None:
            for t in tiers:  # cumulative stacking (verified semantic, fact #4)
                if v >= t.threshold:
                    bonuses += _d(t.points)
    comps["bonuses"] = bonuses

    def_tiers = Decimal("0")
    if line.points_allowed is not None:
        def_tiers += _tier_points(line.points_allowed, cfg.defense.points_allowed_tiers)
    if line.yards_allowed is not None:
        def_tiers += _tier_points(line.yards_allowed, cfg.defense.yards_allowed_tiers)
    comps["def_tiers"] = def_tiers
    return comps


def score_stat_line(line: StatLine, cfg: ScoringConfig) -> Decimal:
    return sum(score_components(line, cfg).values(), Decimal("0"))
