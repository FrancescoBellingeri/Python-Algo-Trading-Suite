import pytest
import pandas as pd
from unittest.mock import MagicMock
from src.execution_handler import ExecutionHandler

@pytest.fixture
def handler():
    mock_conn = MagicMock()
    # Capitale infinito per non fallire sui controlli di rischio in questi test
    return ExecutionHandler(mock_conn, capital=1_000_000)

def test_entry_signal_valid(handler):
    """Caso perfetto: WillR ipervenduto (-90) e Trend rialzista (Close > SMA)."""
    df = pd.DataFrame([{
        'close': 105,
        'SMA_200': 100,    # Trend UP
        'WILLR_10': -90,   # Ipervenduto (< -80)
        'ATR_14': 1.5
    }])
    
    # Deve provare ad entrare (ritorna True o chiama open_long)
    # Nota: check_entry_signals chiama open_long_position che ritorna un booleano
    # Dobbiamo mockare open_long_position per isolare il test del segnale
    handler.open_long_position = MagicMock(return_value=True)
    
    result = handler.check_entry_signals(df)
    
    assert result is True
    handler.open_long_position.assert_called_once()

def test_entry_signal_no_trend(handler):
    """Caso Trend ribassista: WillR OK, ma Prezzo SOTTO la SMA."""
    df = pd.DataFrame([{
        'close': 95,
        'SMA_200': 100,    # Trend DOWN (Close < SMA)
        'WILLR_10': -90,   # Ipervenduto
        'ATR_14': 1.5
    }])
    
    handler.open_long_position = MagicMock()
    result = handler.check_entry_signals(df)
    
    assert result is False
    handler.open_long_position.assert_not_called()

def test_entry_signal_not_oversold(handler):
    """Caso No Ipervenduto: Trend OK, ma WillR troppo alto."""
    df = pd.DataFrame([{
        'close': 105,
        'SMA_200': 100,
        'WILLR_10': -50,   # Non è < -80
        'ATR_14': 1.5
    }])
    
    handler.open_long_position = MagicMock()
    result = handler.check_entry_signals(df)
    assert result is False

def test_exit_signal_triggered(handler):
    """Verifica uscita: WillR sale sopra -20 e Prezzo sotto SMA."""
    df = pd.DataFrame([{
        'close': 90,
        'SMA_200': 100,    # Prezzo crollato sotto SMA
        'WILLR_10': -10,   # Rimbalzo tecnico (> -20)
        'ATR_14': 1.5
    }])
    
    # Simuliamo di avere una posizione aperta
    handler.has_position = MagicMock(return_value=True)
    handler.close_position = MagicMock(return_value=True)
    
    result = handler.check_exit_signals(df)
    assert result is True