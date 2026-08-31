import pytest

from ffi.ingest.gates import (
    SanityGateError,
    check_fieldset,
    check_nonzero_coverage,
    check_rank_correlation,
    union_keys,
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


def test_union_keys_collects_keys_from_every_record_not_just_the_first():
    # The ragged-payload case: record 0 is not representative.
    records = [{"a": 1}, {"a": 1, "b": 2}, {"c": 3}]
    assert union_keys(records) == ["a", "b", "c"]


def test_union_keys_of_nothing_is_empty():
    assert union_keys([]) == []


def test_fieldset_on_unions_catches_drift_that_misses_record_zero():
    prev = [{"player_id": "1", "count": 5}, {"player_id": "2", "count": 4, "x": 1}]
    curr = [{"player_id": "1", "count": 5}, {"player_id": "2", "count": 4}]
    with pytest.raises(SanityGateError, match=r"removed=\['x'\]"):
        check_fieldset(union_keys(prev), union_keys(curr), feed="t")


def test_nonzero_coverage_raises_a_gate_error_naming_a_non_numeric_value():
    # A count of "n/a" is upstream type drift — the gate's business — so it
    # must not escape as a bare ValueError from inside the coercion.
    rows = [{"player_id": "8800", "c": 5}, {"player_id": "4321", "c": "n/a"}]
    with pytest.raises(SanityGateError) as exc:
        check_nonzero_coverage(rows, feed="t", value_key="c", min_players=1)
    assert "player_id='4321'" in str(exc.value)
    assert "'n/a'" in str(exc.value)
    assert "'c'" in str(exc.value)


def test_nonzero_coverage_names_a_bad_record_by_position_when_it_has_no_id():
    with pytest.raises(SanityGateError, match="record #1"):
        check_nonzero_coverage(
            [{"c": 1}, {"c": {}}], feed="t", value_key="c", min_players=1
        )


def test_rank_correlation_raises_a_gate_error_on_a_non_numeric_value():
    prev = {str(i): float(i) for i in range(30)}
    curr = dict(prev)
    curr["7"] = "unknown"
    with pytest.raises(SanityGateError) as exc:
        check_rank_correlation(prev, curr, feed="t")
    assert "key '7'" in str(exc.value)
    assert "'unknown'" in str(exc.value)
