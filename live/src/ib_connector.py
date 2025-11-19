from ib_insync import IB
from src.logger import logger
from config import IB_HOST, IB_PORT, IB_CLIENT_ID

class IBConnector:
    """Gestisce la connessione a Interactive Brokers."""
    
    def __init__(self):
        self.ib = IB()
        self.connected = False
        
    async def connect(self):
        """Connette a TWS/IB Gateway."""
        try:
            await self.ib.connectAsync(
                host=IB_HOST,
                port=IB_PORT,
                clientId=IB_CLIENT_ID
            )
            
            self.ib.sleep(1) 

            self.connected = True
            logger.info(f"Connesso a IB su {IB_HOST}:{IB_PORT}")
            return True
        except Exception as e:
            logger.error(f"Errore connessione: {e}")
            self.connected = False
            return False
    
    def disconnect(self):
        """Disconnette da IB."""
        if self.connected:
            self.ib.disconnect()
            self.connected = False
            logger.info("Disconnesso da IB")
    
    def is_connected(self):
        """Verifica se è connesso."""
        return self.ib.isConnected()