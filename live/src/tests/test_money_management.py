import pytest
from unittest.mock import MagicMock
from src.execution_handler import ExecutionHandler

# 1. Creiamo un connector finto (Mock)
# Serve perché non vogliamo che il test provi a connettersi davvero a IBKR
@pytest.fixture
def mock_connector():
    connector = MagicMock()
    connector.ib = MagicMock()
    return connector

# 2. Testiamo il calcolo della size (LA COSA PIÙ IMPORTANTE)
def test_calculate_position_size_standard(mock_connector):
    handler = ExecutionHandler(mock_connector, capital=100_000)
    
    # Caso: Entrata 100, Stop 95 (Rischio $5/azione). Rischio totale 1% ($1000).
    # Calcolo: 1000 / 5 = 200 azioni.
    size = handler.calculate_position_size(
        entry_price=100,
        stop_loss=95,
        account_size=100_000,
        risk_per_trade_pct=0.01,
        leverage=4
    )
    assert size == 200, f"Errore calcolo standard. Atteso 200, ottenuto {size}"

def test_calculate_position_size_leverage_limit(mock_connector):
    handler = ExecutionHandler(mock_connector, capital=10_000)
    
    # Caso: Stop loss vicinissimo (rischio basso), che suggerirebbe una size enorme.
    # Entrata 100, Stop 99.9 (Rischio $0.1). Rischio conto $100.
    # Size teorica rischio = 100 / 0.1 = 1000 azioni.
    # Valore nozionale = 1000 * 100 = $100,000.
    # MA abbiamo solo 10k e leva 4 -> Max buying power 40k -> Max 400 azioni.
    
    size = handler.calculate_position_size(
        entry_price=100,
        stop_loss=99.9,
        account_size=10_000,
        risk_per_trade_pct=0.01,
        leverage=4
    )
    
    # Il test deve fallire se il bot prova a comprare più di quanto la leva permette
    assert size <= 400, f"Il bot ha ignorato la leva massima! Size: {size}"

def test_calculate_position_size_zero_division(mock_connector):
    handler = ExecutionHandler(mock_connector)
    
    # Caso: Stop loss = Entry price (Rischio 0). Non deve crashare (DivisionByZero).
    size = handler.calculate_position_size(100, 100, 10000, 0.01)
    
    assert size == 0, "Il bot doveva restituire 0 size in caso di rischio nullo"

def test_check_entry_logic(mock_connector):
    """Verifica che non entri se l'ATR è rotto"""
    handler = ExecutionHandler(mock_connector)
    
    # Simuliamo un dataframe con una riga
    import pandas as pd
    df = pd.DataFrame([{
        'close': 100, 
        'SMA_200': 90, 
        'WILLR_10': -90, 
        'ATR_14': 0  # <--- ATR rotto o nullo
    }])
    
    # Dovrebbe ritornare False e non crashare
    result = handler.check_entry_signals(df)
    assert result is False