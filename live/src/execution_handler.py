import pandas as pd
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from ib_insync import Stock, MarketOrder, StopOrder
from src.logger import logger
from src.database import DatabaseHandler
from src.redis_publisher import redis_publisher
from config import SYMBOL, EXCHANGE, CURRENCY, MAX_RISK_PER_TRADE, ATR_MULTIPLIER

class ExecutionHandler:
    """Handles order execution based on Daily Range and HMM prediction."""
    
    def __init__(self, ib_connector, capital=25000):
        """
        Initializes ExecutionHandler.
        
        Args:
            ib_connector: Active IB connection
            capital: Capital for size calculation (default 25k)
        """
        self.ib = ib_connector.ib
        self.db = DatabaseHandler()
        self.contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
        self.capital = capital
        self.base_risk = MAX_RISK_PER_TRADE
        
        # Tracking
        self.current_position = None
        self.current_stop_order = None
        self.entry_price = None
        self.entry_time = None
        self.stop_price = None
        self.position_size = 0

        self.broadcast_position_update()

        self.atr_multiplier = ATR_MULTIPLIER
        self.last_available_funds = 0.0
        
        # Send initial info to dashboard
        logger.info(f"ExecutionHandler initialized - Capital: ${capital:,.0f}")
        redis_publisher.log("info", f"💰 ExecutionHandler initialized - Capital: ${capital:,.0f}")

    def get_available_margin(self):
        """
        Recupera i fondi disponibili effettivi (AvailableFunds) dall'account.
        Questo valore indica quanto margine libero hai per aprire nuove posizioni.
        """
        try:
            # Richiede un aggiornamento rapido del sommario account
            tags = 'AvailableFunds,NetLiquidation'
            summary = self.ib.accountSummary(group='All', tags=tags)
            
            avail_funds = 0.0
            for item in summary:
                if item.tag == 'AvailableFunds' and item.currency == 'EUR': # O 'USD' in base al tuo conto base
                    avail_funds = float(item.value)
                    self.last_available_funds = avail_funds
                if item.tag == 'NetLiquidation' and item.currency == 'EUR':
                    self.capital = float(item.value)
            
            return avail_funds
        except Exception as e:
            logger.error(f"Error fetching account margin: {e}")
            return self.last_available_funds
    
    def calculate_position_size(self, entry_price, stop_loss, leverage=1.95):
        """
        Calculates the number of contracts (or shares) to buy considering:
        - risk per trade in percentage,
        - leverage,
        - maximum absolute loss allowed in dollars.
        """

        # 1. Fetch available margin
        available_funds = self.get_available_margin()
        
        if available_funds <= 0:
            logger.error(f"Available Funds too low: {available_funds}")
            redis_publisher.send_error(f"Available Funds too low: {available_funds}")
            return 0

        max_trade_value_margin = available_funds * 0.70
        max_shares_margin = int(max_trade_value_margin / entry_price)

        # Risk per contract
        R = abs(entry_price - stop_loss)
        if R == 0 or R < 0.01:  # minimal symbolic risk to avoid division by zero
            return 0

        risk_dollars = self.capital * self.base_risk
        risk_based_size = risk_dollars / R
        
        final_size = min(risk_based_size, max_shares_margin)

        return final_size
    
    def check_entry_signals(self, df):
        """
        Executes strategy based on last retrieved candle.
        
        Args:
            df: DataFrame with information to execute strategy
            
        Returns:
            bool: True if order was placed
        """
        
        if self.has_position():
            return False
        
        last_candle = df.iloc[-1]
        if last_candle['WILLR_10'] < -80 and last_candle['close'] > last_candle['SMA_200']:
            entry_price = last_candle['close']
            
            atr_value = last_candle['ATR_14']

            if atr_value <= 0:
                logger.error("ATR < 0, impossible to execute trade")
                redis_publisher.send_error("Invalid ATR, trade cancelled")
                return False
            
            risk_per_share = atr_value * self.atr_multiplier
            # Set initial stop loss
            trailing_stop_price = round(entry_price - risk_per_share, 2)
        
            shares = self.calculate_position_size(
                    entry_price=entry_price,
                    stop_loss=trailing_stop_price,
                    leverage=1.95
                )
        
            if shares <= 0:
                logger.warning("Position size = 0, no trade")
                redis_publisher.log("warning", "⚠️ Position size = 0, trade cancelled")
                return False

            # Place order
            return self.open_long_position(shares, trailing_stop_price)

        return False
    
    def check_exit_signals(self, df):
        """
        Checks if conditions exist to close the trade.
        
        Args:
            df: DataFrame with information to execute strategy
            
        Returns:
            bool: True if trade was closed 
        """
        
        if not self.has_position():
            logger.warning("No open positions")
            return False
        
        last_candle = df.iloc[-1]
        if last_candle['WILLR_10'] > -20 and last_candle['close'] < last_candle['SMA_200']:
            return self.close_position()

        return False
    
    def open_long_position(self, shares, stop_price, attempt=1):
        """
        Opens a long position using a BRACKET ORDER (Parent + Child).
        """
        if attempt > 3:
            logger.error("❌ Max retries reached. Order aborted.")
            redis_publisher.send_error("Max retries reached. Order aborted.")
            return False

        try:
            logger.info(f"📈 Sending Bracket Order: Buy {shares} @ MKT, Stop @ {stop_price}")
            redis_publisher.log("info", f"📈 Sending order: BUY {shares} shares @ MARKET, Stop Loss @ ${stop_price:.2f}")

            # 1. Parent Order (Entry)
            parent = MarketOrder('BUY', shares)
            parent.transmit = False # <--- DO NOT SEND YET!
            parent.tif = 'GTC'
            
            # 2. Child Order (Stop Loss)
            stop_loss = StopOrder('SELL', shares, stop_price)
            stop_loss.outsideRth = False
            stop_loss.tif = 'GTC'
            stop_loss.parentId = parent.orderId
            stop_loss.transmit = True # <--- This will send the whole package
            
            parent_trade = self.ib.placeOrder(self.contract, parent)
            stop_trade = self.ib.placeOrder(self.contract, stop_loss)
            
            logger.info(f"Orders sent. Parent ID: {parent.orderId}, Stop ParentId: {stop_loss.parentId}")
            redis_publisher.log("info", f"📤 Orders sent - Parent ID: {parent.orderId}")

            # 5. Wait for parent FILL confirmation
            self.ib.sleep(1)
            
            status = parent_trade.orderStatus.status
            
            if status in ['Filled', 'PreSubmitted', 'Submitted']:
                # Successo!
                if status == 'Filled':
                    fill_price = parent_trade.orderStatus.avgFillPrice
                    self.entry_price = fill_price
                else:
                    self.entry_price = self.contract.marketPrice() or stop_price + (stop_price*0.01) # fallback

                self.entry_time = datetime.now()
                self.position_size = shares
                self.current_stop_order = stop_loss
                self.stop_price = stop_price
                self.current_position = parent_trade
                
                logger.info(f"✅ Order Accepted/Filled. Size: {shares}")
                redis_publisher.log("success", f"✅ POSITION OPENED: {shares} shares")
                self.broadcast_position_update()
                return True
            elif status in ['Inactive', 'Cancelled', 'PendingCancel']:
                # Fallimento (Probabile errore Margine o altro)
                reason = parent_trade.log[-1].message if parent_trade.log else "Unknown reason"
                logger.warning(f"⚠️ Order Rejected: {status}. Reason: {reason}")
                redis_publisher.log("warning", f"⚠️ Order Rejected: {status}. Reason: {reason}")
                
                # --- RETRY LOGIC ---
                # Riduciamo la size del 10% e riproviamo
                new_shares = int(shares * 0.90)
                if new_shares < 1:
                    return False
                
                logger.info(f"🔄 Retrying with reduced size: {new_shares} shares...")
                redis_publisher.log("warning", f"🔄 Retry {attempt}/3: Reducing size to {new_shares}")
                
                return self.open_long_position(new_shares, stop_price, attempt + 1)

            else:
                # Stati transitori, consideriamo inviato
                return True

        except Exception as e:
            logger.error(f"Bracket Order Error: {e}")
            redis_publisher.send_error(f"Position opening error: {str(e)}")
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
                logger.warning("No open position")
                return False
            
            last_candle = df.iloc[-1]
            atr_value = last_candle['ATR_14']

            if atr_value <= 0:
                logger.error("ATR < 0, impossible to update stop loss")
                redis_publisher.send_error("ATR < 0, impossible to update stop loss")
                return False
            
            risk_per_share = atr_value * self.atr_multiplier
            # Set initial stop loss
            new_stop_price = round(last_candle['close'] - risk_per_share, 2)
            
            if new_stop_price <= self.stop_price:
                logger.debug(f"New stop ${new_stop_price:.2f} not better than current ${self.stop_price:.2f}")
                redis_publisher.log("success", f"New stop ${new_stop_price:.2f} not better than current ${self.stop_price:.2f}")
                return False
            
            self.current_stop_order.auxPrice = new_stop_price
        
            # Re-applying the order updates the existing one
            trade = self.ib.placeOrder(self.contract, self.current_stop_order)
            
            # Update references
            old_stop = self.stop_price
            self.stop_price = new_stop_price
            
            logger.info(f"📈 Stop Loss updated: ${old_stop:.2f} → ${new_stop_price:.2f}")
            redis_publisher.log("success", f"📈 TRAILING STOP: ${old_stop:.2f} → ${new_stop_price:.2f} (+${new_stop_price - old_stop:.2f})")
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating stop loss: {e}")
            redis_publisher.send_error(f"Stop update error: {str(e)}")
            return False
        
    def close_position(self):
        """Closes current position at market."""
        try:
            if not self.has_position():
                logger.warning("No position to close")
                return False
            
            # Place closing market order
            close_order = MarketOrder('SELL', self.position_size)
            trade = self.ib.placeOrder(self.contract, close_order)
            
            # Wait for execution
            self.ib.sleep(1)
            
            if trade.orderStatus.status == 'Filled':
                exit_price = trade.orderStatus.avgFillPrice
                pnl = (exit_price - self.entry_price) * self.position_size
                
                logger.info(f"✅ Position closed @ ${exit_price:.2f}")
                logger.info(f"💰 P&L: ${pnl:.2f} ({pnl/self.capital*100:.2f}%)")

                # Send trade result to dashboard
                redis_publisher.log("success", f"✅ POSITION CLOSED @ ${exit_price:.2f} - P&L: ${pnl:.2f})")

                self.db.save_trade(
                    symbol=SYMBOL,
                    entry_price=self.entry_price,
                    exit_price=exit_price,
                    quantity=self.position_size,
                    entry_time=self.entry_time,
                    exit_time=datetime.now(),
                    pnl_dollar=pnl,
                    pnl_percent=pnl/self.capital*100,
                    exit_reason="EMA_CROSS"
                )
                
                # Reset tracking
                self.current_position = None
                self.current_stop_order = None
                self.entry_price = None
                self.stop_price = None
                self.position_size = 0

                self.broadcast_position_update()
                
                return True
            
            logger.error(f"Closure failed: {trade.orderStatus.status}")
            redis_publisher.send_error(f"Position closure failed: {trade.orderStatus.status}")

            return False
            
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            redis_publisher.send_error(f"Closure error: {str(e)}")
            return False
        
    def has_position(self):
        """Checks if we have an open position."""
        positions = self.ib.positions()
        
        for position in positions:
            if position.contract.symbol == SYMBOL and position.position != 0:
                return True
        
        return False
    
    def update_capital(self):
        """
        Updates self.capital retrieving NetLiquidation value from IB account.
        """
        try:
            # Wait for account data to be available
            self.ib.reqAccountSummary()
            account_values = self.ib.accountValues()
            
            # Search for 'NetLiquidation' value for account base currency (e.g. USD)
            net_liquidation_value = None
            for value in account_values:
                if value.tag == 'NetLiquidation' and value.currency == 'EUR': # Ensure currency is correct
                    net_liquidation_value = float(value.value)
                    break
            
            if net_liquidation_value is not None:
                old_capital = self.capital
                self.capital = net_liquidation_value
                logger.info(f"Capital updated successfully: ${self.capital:,.2f}")
                redis_publisher.log("success", f"✅ Capital updated: ${self.capital:,.2f} (change: ${self.capital - old_capital:+,.2f})")

                return True
            else:
                logger.error("Unable to find 'NetLiquidation' value in account data.")
                redis_publisher.log("error", "❌ NetLiquidation not found in account data")
                redis_publisher.send_error("Unable to update capital: NetLiquidation not found")
                return False

        except Exception as e:
            logger.error(f"Error updating capital: {e}")
            redis_publisher.send_error(f"Capital update error: {str(e)}")
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
            portfolio = self.ib.portfolio()
            pnl = 0.0
            market_value = 0.0
            market_price = 0.0
            
            for item in portfolio:
                if item.contract.symbol == SYMBOL:
                    pnl = item.marketValue - (item.averageCost * item.position)
                    market_value = item.marketValue
                    market_price = item.marketPrice
                    break
            
            # Construct position object matching dashboard expectations
            position_data = {
                "symbol": SYMBOL,
                "shares": self.position_size,
                "entry_price": self.entry_price,
                "current_price": market_price,
                "market_value": market_value,
                "unrealized_pnl": pnl,
                "current_stop": self.stop_price,
                "current_trailing_stop": self.stop_price,
                "current_sma_value": current_ema_value,
                "timestamp": pd.Timestamp.now().isoformat()
            }
            
            # Send as list (dashboard expects list of positions)
            redis_publisher.send_position_update([position_data])
            return position_data

        except Exception as e:
            logger.error(f"Error broadcasting position update: {e}")
            return None