from src.ib_connector import IBConnector
from src.data_handler import DataHandler
from src.indicator_calculator import IndicatorCalculator
from src.logger import logger

# Connetti a IB
connector = IBConnector()
if not connector.connect():
    logger.error("Impossibile connettersi")
    exit(1)

# Crea il DataHandler
data_handler = DataHandler(connector)
indicator_calculator = IndicatorCalculator()

# Test 1: Download iniziale (commentato dopo la prima esecuzione)
# logger.info("\n=== Download Iniziale ===")
# df = data_handler.download_historical_data()
# print(df.tail())

# Test 2: Update ogni 5 minuti (commentato dopo la prima esecuzione)
logger.info("\n=== Update data ===")
df = data_handler.update_data()
#indicator_calculator.calculate_all(df)
df = indicator_calculator.calculate_incremental(df)
print(df.tail())

# Disconnetti
connector.disconnect()