"""Small synthetic-data unit tests for the residual/bucketing math in
`scripts/research_team_change_residuals.py` (the team-change GATE study).
No DB access -- `scripts/` is already on `sys.path` via `tests/conftest.py`.
"""
from research_team_change_residuals import (
    PlayerRecord,
    assign_position_ranks,
    bootstrap_diff_ci,
    classify,
    compare_groups,
    expected_points,
    fit_expectation,
    verdict,
)


def test_assign_position_ranks_orders_by_ecr_and_buckets_by_six():
    rows = [
        {"ecr": v, "name": f"p{int(v)}"} for v in [5.0, 1.0, 3.0, 2.0, 4.0, 6.0, 7.0]
    ]
    ranked = assign_position_ranks(rows)
    assert [r["rank"] for r in ranked] == [1, 2, 3, 4, 5, 6, 7]
    assert [r["name"] for r in ranked] == ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
    # ranks 1-6 -> bucket 1, rank 7 -> bucket 2
    assert [r["bucket"] for r in ranked] == [1, 1, 1, 1, 1, 1, 2]


def test_classify_rookie_unknown_changer_stayer():
    assert classify(prev_team=None, this_team="KC") == "rookie"
    assert classify(prev_team="KC", this_team=None) == "unknown_current_team"
    assert classify(prev_team="KC", this_team="MIN") == "changer"
    assert classify(prev_team="KC", this_team="KC") == "stayer"


def _rec(position, bucket, actual):
    return PlayerRecord(
        season=2023,
        position=position,
        name="synthetic",
        gsis_id="00-SYNTH",
        rank=1,
        bucket=bucket,
        actual=actual,
        classification="stayer",
    )


def test_fit_expectation_is_median_per_position_bucket():
    train = [
        _rec("QB", 1, 100),
        _rec("QB", 1, 200),
        _rec("QB", 1, 300),
        _rec("RB", 1, 10),
    ]
    expectation = fit_expectation(train)
    assert expectation[("QB", 1)] == 200
    assert expectation[("RB", 1)] == 10


def test_expected_points_falls_back_to_nearest_trained_bucket():
    train = [_rec("QB", 1, 50), _rec("QB", 3, 90)]
    expectation = fit_expectation(train)
    # bucket 2 untrained -> nearest of {1, 3} is a tie; min() picks the first
    # by iteration order over sorted(available), i.e. bucket 1.
    assert expected_points("QB", 2, expectation) == 50
    # bucket 4 untrained -> nearest trained bucket is 3.
    assert expected_points("QB", 4, expectation) == 90


def test_bootstrap_diff_ci_deterministic_and_zero_variance_collapses():
    # constant groups -> every bootstrap resample has the same mean, so the
    # CI collapses to a point at the true difference.
    a = [10.0] * 20
    b = [0.0] * 20
    diff, lo, hi = bootstrap_diff_ci(a, b, seed=42, n_boot=500)
    assert diff == 10.0
    assert lo == 10.0
    assert hi == 10.0
    # same seed -> identical result (determinism)
    diff2, lo2, hi2 = bootstrap_diff_ci(a, b, seed=42, n_boot=500)
    assert (diff2, lo2, hi2) == (diff, lo, hi)


def test_compare_groups_and_verdict_priced_vs_mispriced():
    # Changers and stayers drawn from the same distribution -> should be PRICED.
    same = [
        {
            "classification": "changer" if i % 2 == 0 else "stayer",
            "residual": float(i % 5),
        }
        for i in range(40)
    ]
    stats_same = compare_groups(same, seed=1)
    assert verdict(stats_same) == "PRICED"

    # Changers uniformly +50 over stayers, tight groups -> CI excludes 0 and
    # |diff| clears the 10 pt/season bar -> MISPRICED.
    skewed = [
        {"classification": "changer", "residual": 50.0 + (i % 3)} for i in range(15)
    ]
    skewed += [
        {"classification": "stayer", "residual": 0.0 + (i % 3)} for i in range(15)
    ]
    stats_skewed = compare_groups(skewed, seed=1)
    v = verdict(stats_skewed)
    assert v.startswith("MISPRICED")
    assert "OUTPERFORM" in v


def test_compare_groups_insufficient_data_when_one_class_empty():
    records = [{"classification": "stayer", "residual": 1.0}]
    stats = compare_groups(records)
    assert stats["insufficient"] is True
    assert verdict(stats) == "INSUFFICIENT DATA"
