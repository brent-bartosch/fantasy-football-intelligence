from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from ffi.scoring.config import load_config_v1
from ffi.scoring.engine import score_components, score_stat_line
from ffi.scoring.statline import StatLine

CFG = load_config_v1()


def test_zay_flowers_style_line():
    # 7 rec, 143 rec yds, 1 rec TD, 5 rec FD, 2 rush att, 8 rush yds
    # = 7 + 14.3 + 6 + 5 + 0.66 + 0.8 + 3 (100+ bonus) = 36.76 (verified vs Yahoo)
    line = StatLine(
        receptions=7,
        rec_yards=143,
        rec_tds=1,
        rec_first_downs=5,
        rush_attempts=2,
        rush_yards=8,
    )
    assert score_stat_line(line, CFG) == Decimal("36.76")


def test_cumulative_bonus_stacking_200_plus():
    # 216 rush yds -> 21.6 + bonuses 3+4+5 = 33.6 (fact #4: cumulative)
    line = StatLine(rush_yards=216)
    assert score_stat_line(line, CFG) == Decimal("33.6")


def test_cumulative_bonus_stacking_150_band():
    line = StatLine(rec_yards=170)  # 17.0 + 3 + 4 = 24.0
    assert score_stat_line(line, CFG) == Decimal("24.0")


def test_passing_line_with_pick_six():
    # 20 comp, 12 inc, 450 yds, 2 TD, 1 INT (pick six):
    # 10 - 6 + 18 + 12 - 2 - 4 + bonuses (300+ =3, 400+ =4) = 35.0
    line = StatLine(
        pass_completions=20,
        pass_incompletions=12,
        pass_yards=450,
        pass_tds=2,
        interceptions=1,
        pick_sixes=1,
    )
    assert score_stat_line(line, CFG) == Decimal("35.0")


def test_negative_game():
    line = StatLine(fumbles=2, fumbles_lost=2, rush_attempts=3, rush_yards=-2)
    # -2 -4 + 0.99 - 0.2 = -5.21
    assert score_stat_line(line, CFG) == Decimal("-5.21")


def test_kicker_line():
    # Aubrey-style: 1x FG40-49, 1x FG50+, 2 PAT = 4 + 5 + 2 = 11 (verified vs Yahoo)
    line = StatLine(fg_40_49=1, fg_50_plus=1, pat_made=2)
    assert score_stat_line(line, CFG) == Decimal("11")


def test_defense_line_chiefs_wk_sample():
    # 3 sacks, 6 TFL, 2 three-and-outs, 27 pts allowed (tier 21-27 = 0),
    # 394 yds allowed (tier 300-399 = 0) = 11 (verified vs Yahoo)
    line = StatLine(
        sacks=3,
        tackles_for_loss=6,
        three_and_outs=2,
        points_allowed=27,
        yards_allowed=394,
    )
    assert score_stat_line(line, CFG) == Decimal("11")


def test_defense_shutout_and_negative_yards():
    line = StatLine(points_allowed=0, yards_allowed=-3)
    assert score_stat_line(line, CFG) == Decimal("30")  # 10 + 20


def test_defense_worst_tiers():
    line = StatLine(points_allowed=38, yards_allowed=520)
    assert score_stat_line(line, CFG) == Decimal("-11")  # -4 + -7


def test_empty_line_scores_zero():
    assert score_stat_line(StatLine(), CFG) == Decimal("0")


def test_components_sum_to_total():
    line = StatLine(receptions=7, rec_yards=143, rec_tds=1, rec_first_downs=5)
    comps = score_components(line, CFG)
    assert sum(comps.values()) == score_stat_line(line, CFG)


def test_unknown_config_weight_key_fails_loud():
    raw = CFG.model_dump()
    raw["offense"]["weights"]["made_up_stat"] = 9
    from ffi.scoring.config import ScoringConfig

    bad = ScoringConfig.model_validate(raw)
    with pytest.raises(KeyError):
        score_stat_line(StatLine(receptions=1), bad)


# --- purity / property tests (ADR Domain 7) ---
finite = st.one_of(
    st.none(),
    st.floats(min_value=-500, max_value=1000, allow_nan=False, allow_infinity=False),
)


@given(
    rec_yards=st.floats(min_value=0, max_value=400, allow_nan=False),
    receptions=st.floats(min_value=0, max_value=20, allow_nan=False),
)
def test_deterministic_and_input_unmutated(rec_yards, receptions):
    line = StatLine(rec_yards=rec_yards, receptions=receptions)
    before = line.model_dump()
    a = score_stat_line(line, CFG)
    b = score_stat_line(line, CFG)
    assert a == b
    assert line.model_dump() == before


@given(
    y1=st.floats(min_value=0, max_value=300, allow_nan=False),
    y2=st.floats(min_value=0, max_value=300, allow_nan=False),
)
def test_monotone_in_rec_yards(y1, y2):
    lo, hi = sorted([y1, y2])
    assert score_stat_line(StatLine(rec_yards=hi), CFG) >= score_stat_line(
        StatLine(rec_yards=lo), CFG
    )
