import pytest
from unittest.mock import MagicMock, call
from src.execution_handler import ExecutionHandler
from ib_insync import MarketOrder, StopOrder

def test_bracket_order_structure():
    """
    Verifies that 2 orders (Parent and Child) are created and correctly linked.
    """
    mock_conn = MagicMock()
    # Simulate that placeOrder returns a fake "Trade" object with status Filled
    mock_trade = MagicMock()
    mock_trade.orderStatus.status = 'Filled'
    mock_trade.orderStatus.avgFillPrice = 100.0
    
    # Simulate every call to placeOrder returning this trade
    mock_conn.ib.placeOrder.return_value = mock_trade
    # Simulate a fake ID
    mock_conn.ib.client.getReqId.return_value = 12345
    
    handler = ExecutionHandler(mock_conn)
    
    # Execute
    handler.open_long_position(shares=100, stop_price=95.0)
    
    # VERIFICATIONS
    # placeOrder must have been called 2 times (Parent + Stop)
    assert mock_conn.ib.placeOrder.call_count == 2
    
    # Retrieve the arguments with which it was called
    calls = mock_conn.ib.placeOrder.call_args_list
    
    # First order (Parent)
    parent_order_arg = calls[0][0][0] # First argument of the first call
    assert isinstance(parent_order_arg, MarketOrder)
    assert parent_order_arg.action == 'BUY'
    assert parent_order_arg.totalQuantity == 100
    assert parent_order_arg.transmit is False # CRITICAL: Must not transmit immediately
    
    # Second order (Stop)
    stop_order_arg = calls[1][0][0]
    assert isinstance(stop_order_arg, StopOrder)
    assert stop_order_arg.action == 'SELL'
    assert stop_order_arg.auxPrice == 95.0
    assert stop_order_arg.transmit is True # The last one transmits