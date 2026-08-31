import pytest

from ffi.ingest.gates import (
    SanityGateError,
    check_fieldset,
    check_nonzero_coverage,
    check_rank_correlation,
)


def test_fieldset_passes_when_identical():
    check_fieldset(["a", "b"], ["b", "a"], feed="t")


def test_fieldset_passes_when_prior_is_absent():
    check_fieldset(None, ["a"], feed="t")


def test_fieldset_raises_on_removed_key():
    with pytest.raises(SanityGateError, match=r"removed=\['b'\]"):
        check_fieldset(["a", "b"], ["a"], feed="t")


def test_fieldset_raises_on_added_key():
    # The Aug-2026 adp_2qb incident is the template: a new key is drift too.
    with pytest.raises(SanityGateError, match=r"added=\['c'\]"):
        check_fieldset(["a"], ["a", "c"], feed="t")


def test_rank_correlation_is_one_for_identical_orderings():
    prev = {str(i): float(i) for i in range(30)}
    assert check_rank_correlation(prev, dict(prev), feed="t") == pytest.approx(1.0)


def test_rank_correlation_survives_small_perturbation():
    prev = {str(i): float(i) for i in range(50)}
    curr = dict(prev)
    curr["0"], curr["1"] = 1.0, 0.0  # swap the bottom two
    assert check_rank_correlation(prev, curr, feed="t") > 0.99


def test_rank_correlation_raises_on_a_reversed_ordering():
    prev = {str(i): float(i) for i in range(30)}
    curr = {str(i): float(30 - i) for i in range(30)}
    with pytest.raises(SanityGateError, match="rank correlation"):
        check_rank_correlation(prev, curr, feed="t")


def test_rank_correlation_raises_on_thin_overlap():
    with pytest.raises(SanityGateError, match="overlap"):
        check_rank_correlation({"a": 1.0}, {"a": 1.0}, feed="t")


def test_rank_correlation_raises_when_all_values_tie():
    prev = {str(i): 1.0 for i in range(30)}
    with pytest.raises(SanityGateError, match="undefined"):
        check_rank_correlation(prev, dict(prev), feed="t")


def test_nonzero_coverage_counts_positive_values_only():
    rows = [{"c": 5}, {"c": 0}, {"c": None}, {"c": 3}]
    assert check_nonzero_coverage(rows, feed="t", value_key="c", min_players=2) == 2


def test_nonzero_coverage_raises_below_floor():
    rows = [{"c": 5}]
    with pytest.raises(SanityGateError, match="1 row"):
        check_nonzero_coverage(rows, feed="t", value_key="c", min_players=10)
