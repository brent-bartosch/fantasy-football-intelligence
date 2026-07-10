import pytest

from ffi.valuation.baseline import compute_baselines, compute_replacement_ranks
from ffi.valuation.tiers import gmm_tiers


def test_replacement_ranks_2qb_league_shape():
    ranks = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 0})
    # 12 teams x 2 QB starters, no hoarding = QB24; FLEX excludes QB here.
    assert ranks["QB"] == 24
    # Default flex_share 0.5/0.4/0.1 over 12 teams x 1 flex slot:
    # RB = 12*2 + round(12*0.5) = 24 + 6 = 30
    # WR = 12*3 + round(12*0.4) = 36 + 5 = 41
    # TE = 12*1 + round(12*0.1) = 12 + 1 = 13
    assert ranks["RB"] == 30
    assert ranks["WR"] == 41
    assert ranks["TE"] == 13
    assert ranks["K"] == 12 and ranks["DEF"] == 12


def test_qb_hoarding_scenario_moves_baseline():
    r0 = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 0})
    r12 = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 12})
    assert r12["QB"] == 36
    assert r12["QB"] == r0["QB"] + 12
    assert r12["RB"] == r0["RB"]  # hoarding QBs doesn't change RB demand


def test_qb_hoarding_scenarios_24():
    r0 = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 0})
    r24 = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 24})
    assert r0["QB"] == 24
    assert r24["QB"] == 48


def test_compute_baselines_picks_nth_best():
    pts = {"QB": sorted([30 - i for i in range(40)], reverse=True)}
    ranks = {"QB": 24}
    base = compute_baselines(pts, ranks)
    assert base["QB"] == pts["QB"][23]


def test_baseline_fails_loud_on_thin_pool():
    with pytest.raises(ValueError, match="fewer players"):
        compute_baselines({"QB": [20.0] * 10}, {"QB": 24})


def test_flex_share_must_sum_to_one():
    with pytest.raises(ValueError, match="flex_share must sum to 1"):
        compute_replacement_ranks(
            {"teams": 12, "flex_share": {"RB": 0.5, "WR": 0.4, "TE": 0.2}}
        )


def test_gmm_tiers_orders_and_covers():
    values = [400, 395, 390, 300, 295, 290, 200, 195, 190, 100, 95, 90]
    tiers = gmm_tiers(values, max_k=6)
    assert len(tiers) == len(values)
    assert tiers[0] == 1  # best player is tier 1
    assert tiers == sorted(tiers)  # descending values -> nondecreasing tier
    assert tiers[-1] > tiers[0]  # more than one tier found


def test_gmm_tiers_rejects_fewer_than_four_values():
    with pytest.raises(ValueError, match="need >=4 values"):
        gmm_tiers([300.0, 200.0, 100.0])


def test_compute_baselines_rejects_unsorted_pool():
    with pytest.raises(ValueError, match="sorted descending"):
        compute_baselines({"QB": [100.0, 300.0, 200.0]}, {"QB": 2})
