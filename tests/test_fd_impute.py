import pytest

from ffi.scoring.fd_impute import FdRates, impute_fd

RATES = FdRates(
    position_rates={
        "RB": {"rush_fd_per_carry": 0.21, "rec_fd_per_rec": 0.42},
        "WR": {"rush_fd_per_carry": 0.30, "rec_fd_per_rec": 0.58},
        "QB": {
            "rush_fd_per_carry": 0.32,
            "rec_fd_per_rec": 0.50,
            "pass_fd_per_cmp": 0.51,
        },
    },
    player_rates={"00-0033280": {"rush_fd_per_carry": 0.25}},
    prior_strength={"rush": 50.0, "rec": 30.0, "pass": 100.0},
)


def test_position_rate_applied():
    out = impute_fd(RATES, "WR", None, carries=0, receptions=100, completions=0)
    assert out["rec_first_downs"] == pytest.approx(58.0)


def test_player_rate_shrinkage():
    # player_rates stores the ALREADY-SHRUNK rate (computed at fit time); impute_fd
    # simply prefers the player rate over the position rate when present.
    out = impute_fd(RATES, "RB", "00-0033280", carries=200, receptions=0, completions=0)
    assert out["rush_first_downs"] == pytest.approx(0.25 * 200)


def test_unknown_position_fails_loud():
    with pytest.raises(KeyError):
        impute_fd(RATES, "LS", None, carries=1, receptions=0, completions=0)
