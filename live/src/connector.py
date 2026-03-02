from ib_insync import IB
from alpaca.trading.client import TradingClient
from src.redis_publisher import redis_publisher
from config import IB_HOST, IB_PORT, IB_CLIENT_ID, ALPACA_API_KEY, ALPACA_SECRET_KEY
from datetime import datetime
import sys

class Connector:
    """Handles connection to Interactive Brokers and Alpaca."""
    
    def __init__(self):
        self.ib = IB()
        self.trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
        self.account_alpaca = None
        self.connected = False
        self.connection_time = None
        self.reconnect_attempts = 0
        
    async def connect_to_ib(self):
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
            
            redis_publisher.log("success", f"✅ Connected to Interactive Brokers on {IB_HOST}:{IB_PORT}")
            
            # Send account info
            self._send_account_info()
            
            # Setup event handlers
            self._setup_event_handlers()
            
            return True
            
        except Exception as e:
            self.connected = False
            self.reconnect_attempts += 1
            redis_publisher.log("error", f"❌ IB connection error: {str(e)}")
            return False
    
    def disconnect_from_ib(self):
        """Disconnects from IB."""
        if self.connected:
            try:
                # Send disconnection notification
                redis_publisher.log("warning", "🔌 Disconnecting from IB...")
                
                self.ib.disconnect()
                self.connected = False
                self.connection_time = None
                
                redis_publisher.log("info", "📴 Disconnected from Interactive Brokers")
                
            except Exception as e:
                redis_publisher.log("error", f"❌ Disconnection error: {str(e)}")
    
    def is_connected(self):
        """Checks if connected and sends update."""
        if not self.ib.client or not self.ib.isConnected():
            self.connected = False
            redis_publisher.log("error", "IB connection lost unexpectedly")
            return False
        return True
    
    def _send_account_info(self):
        """Sends account information to dashboard."""
        try:
            # Get account info
            self.account_alpaca = self.trading_client.get_account()
            
            if self.account_alpaca:
                # Send to dashboard
                redis_publisher.send_account_update(self.account_alpaca.model_dump())
                
                redis_publisher.log("info", f"💰 Account - Buying Power: ${self.account_alpaca.buying_power}")
            else:
                redis_publisher.log("warning", "⚠️ Unable to retrieve account info")
        except Exception as e:
            redis_publisher.log("warning", f"⚠️ Unable to retrieve account info: {str(e)}")
    
    def _setup_event_handlers(self):
        """Setup event handlers for IB."""
        try:
            # Handler for IB errors
            def on_error(reqId, errorCode, errorString, contract):
                if errorCode < 2000:  # Critical errors
                    redis_publisher.log("error", f"IB Error {errorCode}: {errorString}")
                elif errorCode not in [2104, 2106, 2107, 2108]:  # Ignore market data farm messages
                    redis_publisher.log("warning", f"IB Warning {errorCode}: {errorString}")
            
            # Handler for disconnection
            def on_disconnected():
                self.connected = False
                redis_publisher.log("error", "❌ IB Disconnected unexpectedly")
                sys.exit(1)
            
            # Register handlers
            self.ib.errorEvent += on_error
            self.ib.disconnectedEvent += on_disconnected
            
            redis_publisher.log("info", "IB event handlers configured")
            
        except Exception as e:
            redis_publisher.log("error", f"Error setting up event handlers: {e}")
    
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
                redis_publisher.log("error", f"Keep-alive error: {e}")
                self.is_connected()  # Will verify and notify if disconnected