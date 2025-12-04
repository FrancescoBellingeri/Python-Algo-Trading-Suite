import pytest
from unittest.mock import MagicMock
from src.execution_handler import ExecutionHandler

# 1. Create a fake connector (Mock)
# Needed because we don't want the test to actually connect to IBKR
@pytest.fixture
def mock_connector():
    connector = MagicMock()
    connector.ib = MagicMock()
    return connector

# 2. Test size calculation (THE MOST IMPORTANT THING)
def test_calculate_position_size_standard(mock_connector):
    handler = ExecutionHandler(mock_connector, capital=100_000)
    
    # Case: Entry 100, Stop 95 (Risk $5/share). Total risk 1% ($1000).
    # Calculation: 1000 / 5 = 200 shares.
    size = handler.calculate_position_size(
        entry_price=100,
        stop_loss=95,
        account_size=100_000,
        risk_per_trade_pct=0.01,
        leverage=4
    )
    assert size == 200, f"Standard calculation error. Expected 200, got {size}"

def test_calculate_position_size_leverage_limit(mock_connector):
    handler = ExecutionHandler(mock_connector, capital=10_000)
    
    # Case: Very close stop loss (low risk), which would suggest a huge size.
    # Entry 100, Stop 99.9 (Risk $0.1). Account risk $100.
    # Theoretical risk size = 100 / 0.1 = 1000 shares.
    # Notional value = 1000 * 100 = $100,000.
    # BUT we only have 10k and leverage 4 -> Max buying power 40k -> Max 400 shares.
    
    size = handler.calculate_position_size(
        entry_price=100,
        stop_loss=99.9,
        account_size=10_000,
        risk_per_trade_pct=0.01,
        leverage=4
    )
    
    # The test should fail if the bot tries to buy more than leverage allows
    assert size <= 400, f"The bot ignored max leverage! Size: {size}"

def test_calculate_position_size_zero_division(mock_connector):
    handler = ExecutionHandler(mock_connector)
    
    # Case: Stop loss = Entry price (Risk 0). Should not crash (DivisionByZero).
    size = handler.calculate_position_size(100, 100, 10000, 0.01)
    
    assert size == 0, "The bot should have returned 0 size in case of zero risk"

def test_check_entry_logic(mock_connector):
    """Verify that it doesn't enter if ATR is broken"""
    handler = ExecutionHandler(mock_connector)
    
    # Simulate a dataframe with one row
    import pandas as pd
    df = pd.DataFrame([{
        'close': 100, 
        'SMA_200': 90, 
        'WILLR_10': -90, 
        'ATR_14': 0  # <--- Broken or null ATR
    }])
    
    # Should return False and not crash
    result = handler.check_entry_signals(df)
    assert result is False