from src.ib_connector import IBConnector
from src.logger import logger

# Crea il connettore
connector = IBConnector()

# Connetti
if connector.connect():
    logger.info("✓ Connessione OK")
    
    # Verifica gli account
    accounts = connector.ib.managedAccounts()
    logger.info(f"Account disponibili: {accounts}")
    
    # Disconnetti
    connector.disconnect()
else:
    logger.error("✗ Connessione fallita")