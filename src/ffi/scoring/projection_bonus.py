"""Weekly threshold-bonus EV for SEASON-horizon projections (R16 refinement,
Phase 3 Task 2). The engine run on a season stat line awards each yardage
bonus at most once, but the league pays them EVERY WEEK. Season scoring
replaces that component with sum-over-weeks gamma-priced EV (calibrated:
Brier 0.0212 vs 0.0259 mean-pricing, Phase 2 Task 9)."""
from ffi.scoring.bonus_pricing import bonus_ev_per_week
from ffi.scoring.config import ScoringConfig
from ffi.scoring.statline import StatLine

PROJ_WEEKS = 17.0  # NFL regular-season games projected (bye already excluded)


def season_bonus_ev(
    line: StatLine, cfg: ScoringConfig, cv: dict, position: str, gsis_id: str | None
) -> float:
    total = 0.0
    for field, tiers in cfg.offense.yardage_bonuses.items():
        season_yards = getattr(line, field)
        if season_yards is None or season_yards <= 0:
            continue
        player_cv = cv["players"].get(gsis_id, {}).get(field) if gsis_id else None
        stat_cv = player_cv or cv["positions"].get(position, {}).get(field)
        if stat_cv is None:
            raise ValueError(
                f"no weekly CV for {position}/{field} — is nflverse history loaded?"
            )
        total += (
            bonus_ev_per_week(season_yards / PROJ_WEEKS, stat_cv, tiers) * PROJ_WEEKS
        )
    return total
