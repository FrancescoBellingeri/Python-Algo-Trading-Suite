import redis
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, List
import config

logger = logging.getLogger(__name__)

class RedisPublisher:
    """Handles message publishing from bot to WebSocket server"""
    
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self.pubsub = None
        self.commands_callback = None
        self.enabled = config.WEBSOCKET_ENABLED
        
        if self.enabled:
            self.connect()
    
    def connect(self) -> bool:
        """Connect to Redis"""
        if not self.enabled:
            logger.info("WebSocket publishing disabled")
            return False
            
        try:
            self.client = redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                decode_responses=True
            )
            
            # Test connection
            self.client.ping()
            logger.info(f"✅ Connected to Redis at {config.REDIS_HOST}:{config.REDIS_PORT}")
            
            # Setup command listener
            self._setup_command_listener()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
            self.enabled = False  # Disable if unable to connect
            return False
    
    def disconnect(self):
        """Disconnect from Redis"""
        if self.pubsub:
            self.pubsub.close()
        if self.client:
            self.client.close()
    
    def publish(self, message_type: str, payload: Dict[str, Any]) -> bool:
        """Publishes message to Redis channel"""
        if not self.enabled or not self.client:
            return False
            
        try:
            message = {
                "type": message_type,
                "payload": payload,
                "timestamp": datetime.now().isoformat()
            }
            
            json_message = json.dumps(message, default=str)
            self.client.publish(config.REDIS_CHANNEL, json_message)
            
            logger.debug(f"Published {message_type} to Redis")
            return True
            
        except Exception as e:
            logger.error(f"Error publishing to Redis: {e}")
            return False
    
    def log(self, level: str, message: str, details: Optional[Dict] = None):
        """Sends log message to server"""
        if not config.SEND_LOGS:
            return
            
        log_entry = {
            "level": level,
            "message": message,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "details": details or {}
        }
        self.publish("log", log_entry)
    
    def send_account_update(self, account_values: Dict[str, Any]):
        """Sends account update from IB account values"""
        # Extract important values
        account_data = {
            "net_liquidation": float(account_values.get('NetLiquidation', 0)),
            "buying_power": float(account_values.get('BuyingPower', 0)),
            "total_cash": float(account_values.get('TotalCashValue', 0)),
            "daily_pnl": float(account_values.get('DailyPnL', 0)),
            "unrealized_pnl": float(account_values.get('UnrealizedPnL', 0)),
            "realized_pnl": float(account_values.get('RealizedPnL', 0)),
            "gross_position_value": float(account_values.get('GrossPositionValue', 0)),
        }
        
        self.publish("account_update", account_data)
        
    def send_position_update(self, positions: List[Dict[str, Any]]):
        """
        Sends position update.
        Handles both IB-style (position, avgCost) and Bot-style (shares, entry_price) formats.
        """
        if not config.SEND_POSITIONS:
            return
            
        # Format positions for dashboard
        formatted_positions = []
        for pos in positions:
            # 1. Normalize Quantity (shares vs position)
            quantity = pos.get("shares", pos.get("position", 0))
            
            # 2. Normalize Entry Price (entry_price vs avgCost)
            entry_price = pos.get("entry_price", pos.get("avgCost", 0))
            
            # 3. Normalize Market Price (current_price vs marketPrice)
            market_price = pos.get("current_price", pos.get("marketPrice", 0))
            
            # 4. Normalize Market Value
            market_value = pos.get("market_value", pos.get("marketValue", 0))
            
            # 5. Normalize PnL
            unrealized_pnl = pos.get("unrealized_pnl", pos.get("unrealizedPNL", 0))
            realized_pnl = pos.get("realized_pnl", pos.get("realizedPNL", 0))

            formatted_pos = {
                "symbol": pos.get("symbol", ""),
                "shares": quantity,          # Dashboard expects 'shares'
                "position": quantity,        # Keep 'position' for backward compatibility
                "entry_price": entry_price,  # Dashboard expects 'entry_price'
                "avg_cost": entry_price,     # Keep 'avg_cost' for backward compatibility
                "current_price": market_price,
                "market_price": market_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                
                # Pass-through extra fields required by Dashboard
                "current_stop": pos.get("current_stop"),
                "current_trailing_stop": pos.get("current_trailing_stop"),
                "current_sma_value": pos.get("current_sma_value"),
                "timestamp": pos.get("timestamp", datetime.now().isoformat())
            }
            formatted_positions.append(formatted_pos)
        
        self.publish("position_update", formatted_positions)
        
    def send_order_update(self, order: Dict[str, Any]):
        """Sends order update"""
        if not config.SEND_ORDERS:
            return
            
        order_data = {
            "order_id": order.get("orderId"),
            "symbol": order.get("symbol", config.SYMBOL),
            "action": order.get("action"),
            "quantity": order.get("totalQuantity"),
            "order_type": order.get("orderType"),
            "limit_price": order.get("lmtPrice"),
            "status": order.get("status"),
            "filled": order.get("filled", 0),
            "remaining": order.get("remaining", 0),
            "avg_fill_price": order.get("avgFillPrice"),
            "last_fill_time": order.get("lastFillTime"),
        }
        self.publish("order_update", order_data)
        
    def send_pnl_update(self, daily_pnl: float, unrealized_pnl: float, realized_pnl: float):
        """Sends P&L update"""
        if not config.SEND_PNL:
            return
            
        pnl_data = {
            "daily_pnl": daily_pnl,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_pnl": daily_pnl + unrealized_pnl + realized_pnl
        }
        self.publish("pnl_update", pnl_data)
        
    def send_error(self, error_msg: str, error_code: Optional[int] = None, details: Optional[Dict] = None):
        """Sends error message"""
        error_data = {
            "message": error_msg,
            "code": error_code,
            "details": details or {},
            "timestamp": datetime.now().isoformat()
        }
        self.publish("error", error_data)
        
    def send_trade_signal(self, signal_type: str, details: Dict[str, Any]):
        """Sends trading signal to dashboard"""
        signal_data = {
            "signal_type": signal_type,  # BUY, SELL, HOLD
            "symbol": config.SYMBOL,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }
        self.publish("trade_signal", signal_data)
        self.log("info", f"Signal: {signal_type} for {config.SYMBOL}", details)
    
    def _setup_command_listener(self):
        """Setup listener for commands from server"""
        def listen_for_commands():
            try:
                self.pubsub = self.client.pubsub()
                self.pubsub.subscribe(config.REDIS_COMMANDS_CHANNEL)
                
                logger.info(f"Listening for commands on {config.REDIS_COMMANDS_CHANNEL}")
                
                for message in self.pubsub.listen():
                    if message['type'] == 'message':
                        try:
                            command = json.loads(message['data'])
                            logger.info(f"Received command: {command}")
                            
                            if self.commands_callback:
                                self.commands_callback(command)
                            else:
                                self._handle_default_command(command)
                                
                        except json.JSONDecodeError as e:
                            logger.error(f"Error decoding command: {e}")
                            
            except Exception as e:
                logger.error(f"Error in command listener: {e}")
        
        # Start in separate thread
        import threading
        thread = threading.Thread(target=listen_for_commands, daemon=True, name="Redis-Command-Listener")
        thread.start()
    
    def _handle_default_command(self, command: Dict[str, Any]):
        """Handles default commands when there is no custom callback"""
        cmd_type = command.get("type")
        
        if cmd_type == "stop":
            logger.warning("Received STOP command from dashboard")
            self.log("warning", "Bot stopped by dashboard command")
            # Here you could implement logic to stop the bot
            
        elif cmd_type == "pause":
            logger.info("Received PAUSE command from dashboard")
            self.log("info", "Bot paused by dashboard command")
            
        elif cmd_type == "resume":
            logger.info("Received RESUME command from dashboard")
            self.log("info", "Bot resumed by dashboard command")
            
        elif cmd_type == "close_positions":
            logger.warning("Received CLOSE_POSITIONS command from dashboard")
            self.log("warning", "Closing all positions by dashboard command")
            
        elif cmd_type == "status":
            logger.info("Status request from dashboard")
            # Send status update
            
        else:
            logger.warning(f"Unknown command type: {cmd_type}")
    
    def set_command_callback(self, callback):
        """Sets callback to handle received commands"""
        self.commands_callback = callback

# Singleton instance
redis_publisher = RedisPublisher()