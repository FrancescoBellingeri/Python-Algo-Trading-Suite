from ib_insync import IB
from src.logger import logger
from src.redis_publisher import redis_publisher
from config import IB_HOST, IB_PORT, IB_CLIENT_ID
from datetime import datetime

class IBConnector:
    """Gestisce la connessione a Interactive Brokers."""
    
    def __init__(self):
        self.ib = IB()
        self.connected = False
        self.connection_time = None
        self.reconnect_attempts = 0
        
    async def connect(self):
        """Connette a TWS/IB Gateway."""
        try:
            # Invia messaggio di tentativo connessione
            redis_publisher.log("info", f"📡 Tentativo connessione a IB {IB_HOST}:{IB_PORT}...")
            redis_publisher.publish("connection-status", {
                "status": "connecting",
                "host": IB_HOST,
                "port": IB_PORT,
                "client_id": IB_CLIENT_ID
            })
            
            await self.ib.connectAsync(
                host=IB_HOST,
                port=IB_PORT,
                clientId=IB_CLIENT_ID
            )
            
            self.ib.sleep(1) 

            self.connected = True
            self.connection_time = datetime.now()
            self.reconnect_attempts = 0
            
            logger.info(f"Connesso a IB su {IB_HOST}:{IB_PORT}")
            
            # Invia conferma connessione alla dashboard
            redis_publisher.log("success", f"✅ Connesso a Interactive Brokers")
            redis_publisher.publish("connection-status", {
                "status": "connected",
                "host": IB_HOST,
                "port": IB_PORT,
                "client_id": IB_CLIENT_ID,
                "connected_at": self.connection_time.isoformat(),
                "is_paper": IB_PORT == 7497  # 7497 = paper trading
            })
            
            # Invia info account
            self._send_account_info()
            
            # Setup event handlers
            self._setup_event_handlers()
            
            return True
            
        except Exception as e:
            self.connected = False
            self.reconnect_attempts += 1
            
            logger.error(f"Errore connessione: {e}")
            
            # Invia errore alla dashboard
            redis_publisher.send_error(f"Connessione IB fallita: {str(e)}", error_code=500)
            redis_publisher.log("error", f"❌ Errore connessione IB: {str(e)}")
            redis_publisher.publish("connection-status", {
                "status": "error",
                "error": str(e),
                "reconnect_attempts": self.reconnect_attempts,
                "host": IB_HOST,
                "port": IB_PORT
            })
            
            return False
    
    def disconnect(self):
        """Disconnette da IB."""
        if self.connected:
            try:
                # Invia notifica disconnessione
                redis_publisher.log("warning", "🔌 Disconnessione da IB...")
                redis_publisher.publish("connection-status", {
                    "status": "disconnecting",
                    "reason": "manual_disconnect"
                })
                
                self.ib.disconnect()
                self.connected = False
                self.connection_time = None
                
                logger.info("Disconnesso da IB")
                
                # Conferma disconnessione
                redis_publisher.log("info", "📴 Disconnesso da Interactive Brokers")
                redis_publisher.publish("connection-status", {
                    "status": "disconnected",
                    "timestamp": datetime.now().isoformat()
                })
                
            except Exception as e:
                logger.error(f"Errore durante disconnessione: {e}")
                redis_publisher.send_error(f"Errore disconnessione: {str(e)}")
    
    def is_connected(self):
        """Verifica se è connesso e invia update."""
        is_connected = self.ib.isConnected()
        
        # Se lo stato è cambiato, notifica
        if is_connected != self.connected:
            self.connected = is_connected
            
            if not is_connected:
                # Connessione persa inaspettatamente
                redis_publisher.log("error", "⚠️ Connessione IB persa!")
                redis_publisher.send_error("Connessione IB persa inaspettatamente")
                redis_publisher.publish("connection-status", {
                    "status": "disconnected",
                    "reason": "connection_lost",
                    "timestamp": datetime.now().isoformat()
                })
            else:
                # Riconnesso
                redis_publisher.log("success", "✅ Riconnesso a IB")
                redis_publisher.publish("connection-status", {
                    "status": "connected",
                    "reason": "reconnected",
                    "timestamp": datetime.now().isoformat()
                })
        
        return is_connected
    
    def _send_account_info(self):
        """Invia informazioni account alla dashboard."""
        try:
            # Ottieni info account
            account_values = self.ib.accountValues()
            account_summary = self.ib.accountSummary()
            
            if account_values:
                # Crea dizionario con valori account
                account_dict = {}
                for av in account_values:
                    account_dict[av.tag] = av.value
                
                # Invia alla dashboard
                redis_publisher.send_account_update(account_dict)
                
                # Log info principali
                net_liq = account_dict.get('NetLiquidation', 'N/A')
                buying_power = account_dict.get('BuyingPower', 'N/A')
                redis_publisher.log("info", f"💰 Account - Net Liq: ${net_liq}, Buying Power: ${buying_power}")
            
            # Info account summary
            if account_summary:
                account_id = account_summary[0].account if account_summary else 'Unknown'
                redis_publisher.publish("account-info", {
                    "account_id": account_id,
                    "is_paper": IB_PORT == 7497,
                    "timestamp": datetime.now().isoformat()
                })
                
        except Exception as e:
            logger.error(f"Errore recupero info account: {e}")
            redis_publisher.log("warning", "⚠️ Impossibile recuperare info account")
    
    def _setup_event_handlers(self):
        """Setup event handlers per IB."""
        try:
            # Handler per errori IB
            def on_error(reqId, errorCode, errorString, contract):
                if errorCode < 2000:  # Errori critici
                    redis_publisher.send_error(f"IB Error {errorCode}: {errorString}", error_code=errorCode)
                    redis_publisher.log("error", f"IB Error {errorCode}: {errorString}")
                elif errorCode not in [2104, 2106, 2107, 2108]:  # Ignora messaggi market data farm
                    redis_publisher.log("warning", f"IB Warning {errorCode}: {errorString}")
            
            # Handler per disconnessione
            def on_disconnected():
                self.connected = False
                redis_publisher.log("error", "❌ IB Disconnesso inaspettatamente")
                redis_publisher.publish("connection-status", {
                    "status": "disconnected",
                    "reason": "unexpected_disconnect",
                    "timestamp": datetime.now().isoformat()
                })
            
            # Registra handlers
            self.ib.errorEvent += on_error
            self.ib.disconnectedEvent += on_disconnected
            
            logger.info("Event handlers IB configurati")
            
        except Exception as e:
            logger.error(f"Errore setup event handlers: {e}")
    
    async def keep_alive(self):
        """Mantiene viva la connessione e invia heartbeat."""
        if self.is_connected():
            try:
                # Request current time per tenere viva la connessione
                server_time = self.ib.reqCurrentTime()
                
                # Invia heartbeat alla dashboard ogni tanto
                if hasattr(self, '_last_heartbeat'):
                    if (datetime.now() - self._last_heartbeat).seconds > 30:
                        redis_publisher.publish("ib-heartbeat", {
                            "connected": True,
                            "server_time": server_time,
                            "uptime_seconds": (datetime.now() - self.connection_time).total_seconds() if self.connection_time else 0
                        })
                        self._last_heartbeat = datetime.now()
                else:
                    self._last_heartbeat = datetime.now()
                    
            except Exception as e:
                logger.error(f"Errore keep-alive: {e}")
                self.is_connected()  # Verificherà e notificherà se disconnesso
    
    def get_connection_info(self):
        """Ritorna info sulla connessione corrente."""
        info = {
            "connected": self.connected,
            "host": IB_HOST,
            "port": IB_PORT,
            "client_id": IB_CLIENT_ID,
            "is_paper": IB_PORT == 7497,
            "connection_time": self.connection_time.isoformat() if self.connection_time else None,
            "uptime_seconds": (datetime.now() - self.connection_time).total_seconds() if self.connection_time else 0
        }
        
        # Invia anche alla dashboard
        redis_publisher.publish("connection-info", info)
        
        return info