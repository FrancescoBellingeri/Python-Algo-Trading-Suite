"""Test del modulo di strategia con la logica esatta del backtest."""

from src.ib_connector import IBConnector
from src.data_handler import DataHandler
from src.strategy_module import RegimePredictor
from src.logger import logger
import pandas as pd

# Connetti e scarica i dati
connector = IBConnector()
if not connector.connect():
    logger.error("Impossibile connettersi")
    exit(1)

data_handler = DataHandler(connector)

# Carica i dati storici
logger.info("Caricamento dati storici...")
daily_data = data_handler.get_daily_data()

if daily_data.empty:
    logger.error("Nessun dato disponibile. Esegui prima download_historical_data()")
    connector.disconnect()
    exit(1)

logger.info(f"Dati caricati: {len(daily_data)} giorni")

# Crea il predittore con i parametri esatti del backtest
predictor = RegimePredictor()

# Esegui la predizione
logger.info("\n=== Analisi HMM ===")
prediction = predictor.train_predict(daily_data)

if prediction:
    logger.info("\n=== RISULTATI PREDIZIONE ===")
    print(f"Predizione: {prediction['prediction']}")
    print(f"Confidenza: {prediction['confidence']:.2%}")
    print(f"\nProbabilità stati:")
    print(f"- Stato corrente: {prediction['pi_current']}")
    print(f"- Stato prossimo: {prediction['pi_next']}")
    print(f"\nReturn medio per stato: {prediction['state_returns']}")
    print(f"Bull state: {prediction['bull_state']}")
    print(f"\nUltimi indicatori:")
    print(f"- ATR Ratio: {prediction['last_atrr']:.4f}")
    print(f"- Return: {prediction['last_return']:.2%}")
else:
    logger.error("Predizione fallita")

# Disconnetti
connector.disconnect()