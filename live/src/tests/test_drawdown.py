import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# src.database pulls in config (env) and the redis_publisher singleton, which
# opens a Redis connection at import time. The drawdown helpers are pure, so we
# stub both out to keep this test hermetic.
sys.modules.setdefault("config", SimpleNamespace(ACTIVE_DB_URL="sqlite://"))
sys.modules.setdefault("src.redis_publisher", SimpleNamespace(redis_publisher=MagicMock()))

from src.database import chronological, compute_drawdown, resolve_starting_equity

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def trade(pnl, capital=100_000.0, i=0, exit_time=..., pnl_percent=...):
    """A trade row shaped like the bot writes it.

    The bot stores pnl_percent = pnl / capital * 100, where capital is the
    equity snapshot taken once per session (execution_handler.py), or 0.0 when
    no snapshot was available.
    """
    if pnl_percent is ...:
        pnl_percent = (pnl / capital * 100) if capital else 0.0
    return SimpleNamespace(
        id=i,
        pnl_dollar=pnl,
        pnl_percent=pnl_percent,
        exit_time=T0 + timedelta(days=i) if exit_time is ... else exit_time,
    )


# The regression case: three bot sessions, each with its own frozen capital
# snapshot, plus two trades booked with no snapshot at all. Equity runs
# 100,000 -> peak 103,800 -> trough 94,300 -> 99,200.
REGRESSION = [
    trade(1200.0, 100_000.0, 0), trade(-400.0, 100_000.0, 1),
    trade(2100.0, 100_000.0, 2), trade(900.0, 100_000.0, 3),
    trade(-1500.0, 103_800.0, 4), trade(-2200.0, 103_800.0, 5),
    trade(-800.0, None, 6),
    trade(-3100.0, 103_800.0, 7), trade(-1900.0, 103_800.0, 8),
    trade(1400.0, 94_300.0, 9), trade(-600.0, 94_300.0, 10),
    trade(2800.0, 94_300.0, 11),
    trade(-450.0, None, 12),
    trade(1750.0, 94_300.0, 13),
]


def test_starting_equity_is_recovered_from_the_first_trade():
    assert resolve_starting_equity(chronological(REGRESSION)) == pytest.approx(100_000.0)


def test_regression_drawdown_matches_the_equity_curve():
    dd, pct, peak = compute_drawdown(chronological(REGRESSION), 100_000.0)

    assert dd == pytest.approx(9500.0)          # 103,800 -> 94,300
    assert pct == pytest.approx(9.152, abs=1e-3)
    assert peak == pytest.approx(103_800.0)

    # The two numbers the old code produced for this same history.
    assert pct != pytest.approx(250.0, abs=0.5)  # % of the cumulative-PnL peak
    assert pct != pytest.approx(8.13, abs=0.01)  # compounded pnl_percent curve


def test_dollar_and_percent_describe_the_same_slide():
    dd, pct, peak = compute_drawdown(chronological(REGRESSION), 100_000.0)
    assert pct == pytest.approx(dd / peak * 100)


def test_trades_without_a_capital_snapshot_still_move_the_curve():
    """pnl_percent == 0 rows carry real dollars; dropping them hid drawdown."""
    with_zeros = chronological(REGRESSION)
    without_zeros = [t for t in with_zeros if t.pnl_percent]

    assert compute_drawdown(with_zeros, 100_000.0)[0] == pytest.approx(9500.0)
    assert compute_drawdown(without_zeros, 100_000.0)[0] == pytest.approx(8700.0)


def test_starting_equity_env_override(monkeypatch):
    monkeypatch.setenv("STARTING_EQUITY", "50000")
    assert resolve_starting_equity(chronological(REGRESSION)) == pytest.approx(50_000.0)


@pytest.mark.parametrize("bad", ["0", "-1", "not-a-number"])
def test_bad_starting_equity_override_falls_back_to_derivation(monkeypatch, bad):
    monkeypatch.setenv("STARTING_EQUITY", bad)
    assert resolve_starting_equity(chronological(REGRESSION)) == pytest.approx(100_000.0)


def test_percent_is_zero_rather_than_fabricated_without_an_equity_base():
    rows = [trade(600.0, None, 0), trade(-9500.0, None, 1)]
    assert resolve_starting_equity(rows) is None

    dd, pct, peak = compute_drawdown(rows, None)
    assert dd == pytest.approx(9500.0)
    assert pct == 0.0        # not 1583%, which is what dividing by the peak gave
    assert peak is None


def test_no_losing_streak_means_no_drawdown():
    rows = [trade(100.0, 100_000.0, i) for i in range(5)]
    assert compute_drawdown(chronological(rows), 100_000.0) == (0.0, 0.0, 100_000.0)


def test_deepest_slide_wins_not_the_first_one():
    rows = [trade(-1000.0, 100_000.0, 0), trade(1000.0, 100_000.0, 1),
            trade(-4000.0, 100_000.0, 2)]
    dd, pct, _ = compute_drawdown(chronological(rows), 100_000.0)
    assert dd == pytest.approx(4000.0)
    assert pct == pytest.approx(4.0)


def test_rows_are_ordered_by_exit_time_not_by_insert_order():
    """A late-inserted older trade must not be appended to the end of the curve."""
    rows = [trade(-5000.0, 100_000.0, 9, exit_time=T0 + timedelta(days=9)),
            trade(5000.0, 100_000.0, 10, exit_time=T0)]
    assert [t.id for t in chronological(rows)] == [10, 9]
    assert compute_drawdown(chronological(rows), 100_000.0)[0] == pytest.approx(5000.0)


def test_null_columns_do_not_crash():
    """live/src/database.py declares every trade column nullable."""
    rows = [trade(1000.0, 100_000.0, 0),
            trade(None, None, 1, pnl_percent=None),
            trade(-3000.0, 100_000.0, 2, exit_time=None)]

    ordered = chronological(rows)
    assert [t.id for t in ordered] == [0, 1, 2]   # the null exit_time sorts last
    assert resolve_starting_equity(ordered) == pytest.approx(100_000.0)
    assert compute_drawdown(ordered, 100_000.0)[0] == pytest.approx(3000.0)
