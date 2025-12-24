from ib_insync import IB
from src.logger import logger
from src.redis_publisher import redis_publisher
from config import IB_HOST, IB_PORT, IB_CLIENT_ID
from datetime import datetime
import time

class IBConnector:
    """Handles connection to Interactive Brokers."""
    
    def __init__(self):
        self.ib = IB()
        self.connected = False
        self.connection_time = None
        self.reconnect_attempts = 0
        
    async def connect(self):
        """Connects to TWS/IB Gateway."""
        try:
            # Send connection attempt message
            redis_publisher.log("info", f"📡 Connection attempt to IB {IB_HOST}:{IB_PORT}...")
            
            await self.ib.connectAsync(
                host=IB_HOST,
                port=IB_PORT,
                clientId=IB_CLIENT_ID,
                timeout=15
            )
            
            self.ib.sleep(1) 

            self.connected = True
            self.connection_time = datetime.now()
            self.reconnect_attempts = 0
            
            logger.info(f"Connected to IB on {IB_HOST}:{IB_PORT}")
            
            # Send connection confirmation to dashboard
            redis_publisher.log("success", f"✅ Connected to Interactive Brokers")
            
            # Send account info
            self._send_account_info()
            
            # Setup event handlers
            self._setup_event_handlers()
            
            return True
            
        except Exception as e:
            self.connected = False
            self.reconnect_attempts += 1
            
            logger.error(f"Connection error: {e}")
            
            # Send error to dashboard
            redis_publisher.send_error(f"IB connection failed: {str(e)}", error_code=500)
            redis_publisher.log("error", f"❌ IB connection error: {str(e)}")
            
            return False
    
    def disconnect(self):
        """Disconnects from IB."""
        if self.connected:
            try:
                # Send disconnection notification
                redis_publisher.log("warning", "🔌 Disconnecting from IB...")
                
                self.ib.disconnect()
                self.connected = False
                self.connection_time = None
                
                logger.info("Disconnected from IB")
                
                # Disconnection confirmation
                redis_publisher.log("info", "📴 Disconnected from Interactive Brokers")
                
            except Exception as e:
                logger.error(f"Error during disconnection: {e}")
                redis_publisher.send_error(f"Disconnection error: {str(e)}")
    
    def is_connected(self):
        """Checks if connected and sends update."""
        if not self.ib.client or not self.ib.isConnected():
            self.connected = False
            redis_publisher.send_error("IB connection lost unexpectedly")
            return False
        return True
    
    def _send_account_info(self):
        """Sends account information to dashboard."""
        try:
            # Get account info
            account_values = self.ib.accountValues()
            
            if account_values:
                # Create dictionary with account values
                account_dict = {}
                for av in account_values:
                    account_dict[av.tag] = av.value
                
                # Send to dashboard
                redis_publisher.send_account_update(account_dict)
                
                # Log main info
                net_liq = account_dict.get('NetLiquidation', 'N/A')
                buying_power = account_dict.get('BuyingPower', 'N/A')
                redis_publisher.log("info", f"💰 Account - Net Liq: ${net_liq}, Buying Power: ${buying_power}")
            else:
                redis_publisher.log("warning", "⚠️ Unable to retrieve account info")
        except Exception as e:
            logger.error(f"Error retrieving account info: {e}")
            redis_publisher.log("warning", "⚠️ Unable to retrieve account info")
    
    def _setup_event_handlers(self):
        """Setup event handlers for IB."""
        try:
            # Handler for IB errors
            def on_error(reqId, errorCode, errorString, contract):
                if errorCode < 2000:  # Critical errors
                    redis_publisher.send_error(f"IB Error {errorCode}: {errorString}", error_code=errorCode)
                    redis_publisher.log("error", f"IB Error {errorCode}: {errorString}")
                elif errorCode not in [2104, 2106, 2107, 2108]:  # Ignore market data farm messages
                    redis_publisher.log("warning", f"IB Warning {errorCode}: {errorString}")
            
            # Handler for disconnection
            def on_disconnected():
                self.connected = False
                redis_publisher.log("error", "❌ IB Disconnected unexpectedly")
            
            # Register handlers
            self.ib.errorEvent += on_error
            self.ib.disconnectedEvent += on_disconnected
            
            logger.info("IB event handlers configured")
            
        except Exception as e:
            logger.error(f"Error setting up event handlers: {e}")
    
    async def keep_alive(self):
        """Keeps connection alive and sends heartbeat."""
        if self.is_connected():
            try:
                # Request current time to keep connection alive
                server_time = self.ib.reqCurrentTime()
                
                # Send heartbeat to dashboard occasionally
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
                logger.error(f"Keep-alive error: {e}")
                self.is_connected()  # Will verify and notify if disconnected
    
    def get_connection_info(self):
        """Returns current connection info."""
        info = {
            "connected": self.connected,
            "host": IB_HOST,
            "port": IB_PORT,
            "client_id": IB_CLIENT_ID,
            "is_paper": IB_PORT == 7497,
            "connection_time": self.connection_time.isoformat() if self.connection_time else None,
            "uptime_seconds": (datetime.now() - self.connection_time).total_seconds() if self.connection_time else 0
        }
        
        # Send also to dashboard
        redis_publisher.publish("connection-info", info)
        
        return info