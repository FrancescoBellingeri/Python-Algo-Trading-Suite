"""Test dell'ExecutionHandler con la logica del backtest."""

from src.ib_connector import IBConnector
from src.execution_handler import ExecutionHandler
from src.logger import logger
from datetime import datetime

# Connetti a IB
connector = IBConnector()
if not connector.connect():
    logger.error("Impossibile connettersi")
    exit(1)

# Crea l'ExecutionHandler
execution = ExecutionHandler(connector, capital=25000)

logger.info("Inizio della giornata di trading. Aggiornamento del capitale...")
if not execution.update_capital():
    logger.error("Fallito l'aggiornamento del capitale. Il bot si ferma per sicurezza.")


# Test 1: Analisi prima candela
logger.info("\n=== Test Analisi Prima Candela ===")
first_candle_bullish = {
    'date': datetime.now(),
    'open': 450.00,
    'high': 451.50,
    'low': 449.50,
    'close': 451.20,  # Bullish
    'volume': 1000000
}

dr_result = execution.analyze_first_candle(first_candle_bullish)
if dr_result:
    logger.info(f"Daily Range calcolato: {dr_result}")

# Test 2: Verifica posizioni esistenti
logger.info("\n=== Test Posizioni ===")
has_pos = execution.has_position()
logger.info(f"Posizioni aperte: {has_pos}")

if has_pos:
    pos_info = execution.get_position_info()
    logger.info(f"Info posizione: {pos_info}")
    pnl = execution.get_current_pnl()
    logger.info(f"P&L corrente: ${pnl:.2f}")

# Test 3: Simula esecuzione strategia (solo display, non esegue)
# logger.info("\n=== Test Strategia (simulato) ===")
# second_candle = {
#     'open': 451.30,
#     'high': 451.80,
#     'low': 451.10,
#     'close': 451.60,
#     'volume': 800000
# }

# # Simula una predizione BULL
# prediction = 'BULL'
# atr_value = 0.02  # 2% ATR ratio

# logger.info(f"Seconda candela: Open={second_candle['open']}")
# logger.info(f"Predizione HMM: {prediction}")
# logger.info(f"ATR Ratio: {atr_value}")

# # Calcola cosa farebbe il sistema (senza eseguire)
# if dr_result and dr_result['direction'] == 'bullish' and prediction == 'BULL':
#     logger.info("Setup: LONG trade")
#     entry = second_candle['open']
#     stop = dr_result['low']
#     risk_per_share = abs(entry - stop)
#     tp = entry + (10 * risk_per_share)
    
#     logger.info(f"Entry: ${entry:.2f}")
#     logger.info(f"Stop Loss: ${stop:.2f}")
#     logger.info(f"Take Profit: ${tp:.2f} (10R)")
    
#     shares = execution.calculate_position_size(entry, stop, 1.0)
#     logger.info(f"Position size: {shares} shares")

# NON eseguire ordini reali durante il test!
# Per eseguire davvero: execution.execute_strategy(second_candle, prediction, atr_value)

# Disconnetti
connector.disconnect()