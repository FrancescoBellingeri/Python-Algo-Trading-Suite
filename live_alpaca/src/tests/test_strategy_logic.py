import pytest
import pandas as pd
from unittest.mock import MagicMock
from src.execution_handler import ExecutionHandler

@pytest.fixture
def handler():
    mock_conn = MagicMock()
    # Infinite capital to avoid failing risk checks in these tests
    return ExecutionHandler(mock_conn, capital=1_000_000)

def test_entry_signal_valid(handler):
    """Perfect case: WillR oversold (-90) and Bullish trend (Close > SMA)."""
    df = pd.DataFrame([{
        'close': 105,
        'SMA_200': 100,    # Trend UP
        'WILLR_10': -90,   # Oversold (< -80)
        'ATR_14': 1.5
    }])
    
    # Should attempt to enter (returns True or calls open_long)
    # Note: check_entry_signals calls open_long_position which returns a boolean
    # We need to mock open_long_position to isolate the signal test
    handler.open_long_position = MagicMock(return_value=True)
    
    result = handler.check_entry_signals(df)
    
    assert result is True
    handler.open_long_position.assert_called_once()

def test_entry_signal_no_trend(handler):
    """Bearish trend case: WillR OK, but Price BELOW SMA."""
    df = pd.DataFrame([{
        'close': 95,
        'SMA_200': 100,    # Trend DOWN (Close < SMA)
        'WILLR_10': -90,   # Oversold
        'ATR_14': 1.5
    }])
    
    handler.open_long_position = MagicMock()
    result = handler.check_entry_signals(df)
    
    assert result is False
    handler.open_long_position.assert_not_called()

def test_entry_signal_not_oversold(handler):
    """Not oversold case: Trend OK, but WillR too high."""
    df = pd.DataFrame([{
        'close': 105,
        'SMA_200': 100,
        'WILLR_10': -50,   # Not < -80
        'ATR_14': 1.5
    }])
    
    handler.open_long_position = MagicMock()
    result = handler.check_entry_signals(df)
    assert result is False

def test_exit_signal_triggered(handler):
    """Verify exit: WillR rises above -20 and Price below SMA."""
    df = pd.DataFrame([{
        'close': 90,
        'SMA_200': 100,    # Price crashed below SMA
        'WILLR_10': -10,   # Technical bounce (> -20)
        'ATR_14': 1.5
    }])
    
    # Simulate having an open position
    handler.has_position = MagicMock(return_value=True)
    handler.close_position = MagicMock(return_value=True)
    
    result = handler.check_exit_signals(df)
    assert result is True