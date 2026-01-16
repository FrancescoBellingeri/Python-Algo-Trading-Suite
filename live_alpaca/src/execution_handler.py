import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, ReplaceOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from src.database import DatabaseHandler
from src.redis_publisher import redis_publisher
from config import SYMBOL, MAX_RISK_PER_TRADE, ATR_MULTIPLIER, ALPACA_API_KEY, ALPACA_SECRET_KEY

class ExecutionHandler:
    """Handles order execution based on Daily Range and HMM prediction."""
    
    def __init__(self):
        """
        Initializes ExecutionHandler.
        
        Args:
            capital: Capital for size calculation (default 25k)
        """
        self.db = DatabaseHandler()
        self.trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
        self.capital = None
        
        # Tracking
        self.current_position = None
        self.current_stop_order = None
        self.entry_price = None
        self.entry_time = None
        self.stop_price = None
        self.position_size = 0

        self.broadcast_position_update()

        self.last_available_funds = 0.0
        
        # Send initial info to dashboard
        redis_publisher.log("info", f"💰 ExecutionHandler initialized")
    
    def calculate_position_size(self, entry_price, stop_loss):
        # 1. Fetch available funds
        account = self.trading_client.get_account()
        self.capital = float(account.cash)

        if self.capital <= 0:
            redis_publisher.log("error", "❌ Sizing failed: Available funds is 0 or negative.")
            return 0

        # 2. Risk Management Calculation
        risk_dollars = self.capital * MAX_RISK_PER_TRADE * 0.95
        risk_per_share = abs(entry_price - stop_loss)
        
        if risk_per_share < 0.01: 
            redis_publisher.log("warning", "❌ Sizing failed: Risk per share too small (Stop too close to Entry).")
            return 0
        
        # Size based on Risk
        shares = int(risk_dollars / risk_per_share)

        usable_bp = float(account.cash) * 0.95  # buffer Alpaca
        shares_by_bp = int(usable_bp / entry_price)
        
        return min(shares, shares_by_bp)
    
    def check_entry_signals(self, df):
        """
        Executes strategy based on last retrieved candle.
        
        Args:
            df: DataFrame with information to execute strategy
            
        Returns:
            bool: True if order was placed
        """
        try:
            if self.has_position():
                return False
        
            last_candle = df.iloc[-1]
            if last_candle['WILLR_10'] < -80 and last_candle['close'] > last_candle['SMA_200']:
                entry_price = float(last_candle['close'])
                atr_value = float(last_candle['ATR_14'])

                if atr_value <= 0:
                    redis_publisher.log("error", "Invalid ATR (< 0), impossible to execute trade")
                    return False
                
                risk_per_share = atr_value * ATR_MULTIPLIER
                # Set initial stop loss
                trailing_stop_price = round(entry_price - risk_per_share, 2)
            
                shares = self.calculate_position_size(
                        entry_price=entry_price,
                        stop_loss=trailing_stop_price
                    )
            
                if shares <= 0:
                    redis_publisher.log("warning", "⚠️ Position size = 0, trade cancelled")
                    return False

            # Place order
            return self.open_long_position(shares, entry_price, trailing_stop_price)

        except Exception as e:
            redis_publisher.log("error", f"❌ Error in check_entry_signals: {str(e)}")
            return False
    
    def check_exit_signals(self, df):
        """
        Checks if conditions exist to close the trade.
        
        Args:
            df: DataFrame with information to execute strategy
            
        Returns:
            bool: True if trade was closed 
        """

        redis_publisher.log("info", "Checking exit signals...")
        
        if not self.has_position():
            redis_publisher.log("warning", "No open positions")
            return False
        
        last_candle = df.iloc[-1]
        if last_candle['WILLR_10'] > -20 and last_candle['close'] < last_candle['SMA_200']:
            self.trading_client.close_all_positions(cancel_orders=True)
            redis_publisher.log("info", "Position closed")
            return True

        return False
    
    def open_long_position(self, shares, entry_price, stop_price, attempt=1):
        """
        Opens a long position using a BRACKET ORDER (Parent + Child).
        """
        if attempt > 3:
            redis_publisher.log("error", "❌ Max retries reached. Order aborted.")
            return False

        try:
            self.entry_price = float(entry_price)
            self.stop_price = float(stop_price)
            self.position_size = int(shares)
            self.entry_time = datetime.now(ZoneInfo("America/New_York"))

            redis_publisher.log("info", f"📈 Sending order: BUY {shares} shares @ MARKET, Stop Loss @ ${stop_price:.2f}")

            market_order_data = MarketOrderRequest(
                symbol=SYMBOL,
                qty=shares,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.GTC,
                order_class="oto",
                stop_loss=StopLossRequest(stop_price=stop_price)
            )

            self.current_position = self.trading_client.submit_order(
                order_data=market_order_data
            )

            all_orders = self.trading_client.get_orders()
            self.current_stop_order = next((o for o in all_orders if o.symbol == SYMBOL and o.type == "stop"), None)

            redis_publisher.log("success", f"✅ POSITION OPENED: {shares} shares @ approx ${self.entry_price:.2f}")
            self.broadcast_position_update()
            return True

        except Exception as e:
            redis_publisher.log("error", f"Error opening position: {str(e)}")
            return False

    def update_trailing_stop(self, df):
        """
        Updates stop loss (manual trailing stop).
        
        Args:
            new_stop_price: New stop price
            
        Returns:
            bool: True if successfully updated
        """
        try:
            if not self.has_position():
                redis_publisher.log("warning", "No open position")
                return False
            
            last_candle = df.iloc[-1]
            atr_value = last_candle['ATR_14']

            if atr_value <= 0:
                redis_publisher.log("error", "ATR < 0, impossible to update stop loss")
                return False
            
            risk_per_share = atr_value * ATR_MULTIPLIER
            # Set initial stop loss
            new_stop_price = round(last_candle['close'] - risk_per_share, 2)
            
            if not self.current_stop_order:
                redis_publisher.log("warning", "Stop order missing in state. Attempting to restore...")
                if not self.restore_stop_loss():
                    return False

            current_stop_val = float(self.current_stop_order.stop_price)

            if new_stop_price <= current_stop_val:
                redis_publisher.log("success", f"New stop ${new_stop_price:.2f} not better than current ${current_stop_val:.2f}")
                return False
            
            replace_data = ReplaceOrderRequest(stop_price=new_stop_price)
            self.current_stop_order = self.trading_client.replace_order_by_id(self.current_stop_order.id, replace_data)
            
            redis_publisher.log("success", f"📈 TRAILING STOP: ${current_stop_val:.2f} → ${new_stop_price:.2f} (+${new_stop_price - current_stop_val:.2f})")
            
            return True
            
        except Exception as e:
            redis_publisher.log("error", f"Stop update error: {str(e)}")
            return False
        
    def check_stop_loss_triggered(self):
        """
        Checks if the stop loss order was triggered/filled.
        If triggered, saves the trade to DB and resets state.
        
        Returns:
            bool: True if stop was triggered, False otherwise
        """
        try:
            # If we don't think we have a position, nothing to check
            if not self.position_size or self.position_size <= 0:
                return False
            
            # Check if we have a tracked stop order
            if not self.current_stop_order:
                return False

            # Get latest status of the stop order
            try:
                stop_order = self.trading_client.get_order_by_id(self.current_stop_order.id)
            except Exception:
                return False

            if stop_order.status == 'filled':
                exit_price = float(stop_order.filled_avg_price) if stop_order.filled_avg_price else self.stop_price
                exit_time = stop_order.filled_at or datetime.now(ZoneInfo("America/New_York"))
                
                # Calculate P&L
                if self.entry_price:
                    pnl = (exit_price - self.entry_price) * self.position_size
                    pnl_percent = (pnl / self.capital) * 100
                else:
                    pnl = 0.0
                    pnl_percent = 0.0
                
                # Log the trade closure
                redis_publisher.log("warning", f"🛑 STOP LOSS TRIGGERED @ ${exit_price:.2f} - P&L: ${pnl:.2f} ({pnl_percent:.2f}%)")
                
                # Save trade to database
                self.db.save_trade(
                    symbol=SYMBOL,
                    entry_price=float(self.entry_price or exit_price),
                    exit_price=float(exit_price),
                    quantity=int(self.position_size),
                    entry_time=self.entry_time,
                    exit_time=exit_time,
                    pnl_dollar=float(pnl),
                    pnl_percent=float(pnl_percent),
                    exit_reason="TRAILING_STOP"
                )
                
                # Reset internal state
                self.current_position = None
                self.current_stop_order = None
                self.entry_price = None
                self.entry_time = None
                self.stop_price = None
                self.position_size = 0
                
                # Notify dashboard
                self.broadcast_position_update()
                
                return True
            
            return False
            
        except Exception as e:
            redis_publisher.log("error", f"Error checking stop loss: {str(e)}")
            return False

    def restore_stop_loss(self):
        """Restores the stop loss order."""
        try:
            if not self.has_position():
                redis_publisher.log("warning", "No open position")
                return False
            
            if self.current_stop_order:
                redis_publisher.log("warning", "Stop loss order already exists")
                return False
            
            df = self.db.get_latest_data(SYMBOL, 10)
            last_candle = df.iloc[-1]
            self.stop_price = round(last_candle['close'] - last_candle['ATR_14'] * ATR_MULTIPLIER, 2)
                    
            stop_order = StopOrderRequest(
                symbol=SYMBOL,
                qty=self.position_size,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=self.stop_price
            )
            self.current_stop_order = self.trading_client.submit_order(order_data=stop_order)
            
            redis_publisher.log("success", f"RESTORED STOP LOSS: ${self.stop_price:.2f}")
            
            return True
            
        except Exception as e:
            redis_publisher.log("error", f"Error restoring stop loss: {str(e)}")
            return False
        
    def has_position(self):
        """Checks if we have an open position."""
        positions = self.trading_client.get_all_positions()
        
        for position in positions:
            if position.symbol == SYMBOL and position.qty != 0:
                return True
        
        return False
        
    def broadcast_position_update(self, current_ema_value=0.0):
        """
        Gathers all position data and sends a standardized update to the dashboard.
        """
        try:
            if not self.has_position():
                # Send empty list to clear dashboard
                redis_publisher.send_position_update([])
                return None

            # Get portfolio data for PnL
            position = self.trading_client.get_open_position(SYMBOL)
            
            # Construct position object matching dashboard expectations
            position_data = {
                "symbol": SYMBOL,
                "shares": position.qty,
                "entry_price": position.avg_entry_price,
                "current_price": position.current_price,
                "market_value": position.market_value,
                "unrealized_pnl": (float(position.current_price) - float(position.avg_entry_price)) * float(position.qty),
                "current_stop": self.stop_price,
                "current_trailing_stop": self.stop_price,
                "current_sma_value": current_ema_value,
                "timestamp": pd.Timestamp.now().isoformat()
            }
            
            # Send as list (dashboard expects list of positions)
            redis_publisher.send_position_update([position_data])
            return position_data

        except Exception as e:
            redis_publisher.log("error", f"Error broadcasting position update: {e}")
            return None