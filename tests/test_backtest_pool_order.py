"""load_backtest_pool must return a deterministic order (Phase 3 Minor: missing ORDER BY)."""
from ffi.sim import backtest


def test_load_backtest_pool_has_order_by():
    # The query text itself must carry an ORDER BY — cheaper and stricter than
    # comparing two live loads (which could agree by accident of heap order).
    import inspect

    src = inspect.getsource(backtest.load_backtest_pool)
    assert "ORDER BY" in src, "load_backtest_pool query needs a deterministic ORDER BY"
