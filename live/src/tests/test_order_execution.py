import pytest
from unittest.mock import MagicMock, call
from src.execution_handler import ExecutionHandler
from ib_insync import MarketOrder, StopOrder

def test_bracket_order_structure():
    """
    Verifica che vengano creati 2 ordini (Parent e Child) e che siano collegati correttamente.
    """
    mock_conn = MagicMock()
    # Simuliamo che placeOrder restituisca un oggetto "Trade" finto con status Filled
    mock_trade = MagicMock()
    mock_trade.orderStatus.status = 'Filled'
    mock_trade.orderStatus.avgFillPrice = 100.0
    
    # Simuliamo che ogni chiamata a placeOrder restituisca questo trade
    mock_conn.ib.placeOrder.return_value = mock_trade
    # Simuliamo un ID finto
    mock_conn.ib.client.getReqId.return_value = 12345
    
    handler = ExecutionHandler(mock_conn)
    
    # Eseguiamo
    handler.open_long_position(shares=100, stop_price=95.0)
    
    # VERIFICHE
    # placeOrder deve essere stato chiamato 2 volte (Parent + Stop)
    assert mock_conn.ib.placeOrder.call_count == 2
    
    # Recuperiamo gli argomenti con cui è stato chiamato
    calls = mock_conn.ib.placeOrder.call_args_list
    
    # Primo ordine (Parent)
    parent_order_arg = calls[0][0][0] # Primo argomento della prima chiamata
    assert isinstance(parent_order_arg, MarketOrder)
    assert parent_order_arg.action == 'BUY'
    assert parent_order_arg.totalQuantity == 100
    assert parent_order_arg.transmit is False # CRITICO: Non deve trasmettere subito
    
    # Secondo ordine (Stop)
    stop_order_arg = calls[1][0][0]
    assert isinstance(stop_order_arg, StopOrder)
    assert stop_order_arg.action == 'SELL'
    assert stop_order_arg.auxPrice == 95.0
    assert stop_order_arg.transmit is True # L'ultimo trasmette