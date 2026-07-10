"""THE golden gate (R1, ADR Domain 7): engine output must EXACTLY equal
Yahoo's official points for every committed fixture."""
import json
import pathlib
from decimal import Decimal

import pytest

from ffi.scoring.config import load_config_v1
from ffi.scoring.engine import score_stat_line
from ffi.scoring.yahoo_adapter import stat_line_from_yahoo

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "golden_2025.json").read_text()
)
CFG = load_config_v1()


@pytest.mark.parametrize(
    "fx", FIXTURES, ids=[f"{f['class']}-{f['name']}-wk{f['week']}" for f in FIXTURES]
)
def test_golden_exact_match(fx):
    line = stat_line_from_yahoo(fx["stats"])
    got = score_stat_line(line, CFG)
    assert got == Decimal(
        fx["total_points"]
    ), f"{fx['name']} wk{fx['week']}: engine={got} yahoo={fx['total_points']}"
