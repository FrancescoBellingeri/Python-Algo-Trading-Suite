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
        
        # Subscription to account data (necessary to populate accountSummary)
        self.ib.reqAccountSummary()

    def get_available_margin(self):
        """
        Retrieves available funds with a retry mechanism and fallback.
        """
        # 0. Helper to find value in a list of IBKR objects
        target_tags = ['AvailableFunds', 'TotalCashValue', 'NetLiquidation', 'BuyingPower', 'CashBalance']
        
        def find_value_in_list(items):
            for item in items:
                if item.tag in target_tags:
                    # Check currency (allow base currency or bot currency)
                    if item.currency == CURRENCY or item.currency == 'BASE' or item.currency == '':
                        try:
                            val = float(item.value)
                            if val > 0: return val
                        except ValueError:
                            continue
            return None

        # 1. Quick Check (Cache/Live)
        val = find_value_in_list(self.ib.accountValues())
        if val: 
            self.last_available_funds = val
            return val

        val = find_value_in_list(self.ib.accountSummary())
        if val:
            self.last_available_funds = val
            return val

        # 2. If 0, Force Wait (The "Kickstart")
        logger.warning("⚠️ Funds appear to be 0. Waiting for data sync (Max 3s)...")
        
        # We try 15 times x 0.2s = 3 seconds max wait
        for i in range(15):
            self.ib.sleep(0.2) # CRITICAL: ib.sleep allows incoming network messages to be processed
            
            # Re-check Summary
            val = find_value_in_list(self.ib.accountSummary())
            if val:
                logger.info(f"✅ Data received after {i*0.2:.1f}s: ${val:,.2f}")
                self.last_available_funds = val
                return val
        
        # 3. DEBUG: If still failing, print what tags WE DO HAVE to the log
        logger.error("❌ TIMEOUT: Could not fetch margin data from IBKR.")
        logger.info("--- DUMPING AVAILABLE TAGS ---")
        found_tags = [f"{x.tag}={x.value} ({x.currency})" for x in self.ib.accountSummary()]
        logger.info(str(found_tags[:10])) # Print first 10 tags
        
        # 4. Fallback (The "Show Must Go On" Fix)
        # If we can't read the balance, we use the self.capital (25k) setting 
        # so the bot doesn't freeze.
        if self.capital > 0:
            logger.warning(f"⚠️ Using fallback capital: ${self.capital:,.2f}")
            return self.capital
            
        return 0.0
    
    def calculate_position_size(self, entry_price, stop_loss):
        # 1. Fetch available funds
        available_funds = self.get_available_margin()

        if available_funds <= 0:
            logger.error("❌ Sizing failed: Available funds is 0 or negative.")
            return 0

        # 2. Risk Management Calculation
        risk_dollars = self.capital * self.base_risk
        risk_per_share = abs(entry_price - stop_loss)
        
        if risk_per_share < 0.01: 
            logger.warning("❌ Sizing failed: Risk per share too small (Stop too close to Entry).")
            return 0
        
        # Size based on Risk
        risk_based_size = int(risk_dollars / risk_per_share)
        margin_based_size = int((available_funds * 0.95) / entry_price)
        
        final_size = min(risk_based_size, margin_based_size)

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
                    stop_loss=trailing_stop_price
                )
        
            if shares <= 0:
                logger.warning("Position size = 0, no trade")
                redis_publisher.log("warning", "⚠️ Position size = 0, trade cancelled")
                return False

            shares_validated = self.validate_order_size(self.contract, shares)

            if shares_validated <= 0:
                logger.warning("❌ Order cancelled after margin check (Size 0).")
                redis_publisher.log("warning", "❌ Order cancelled after margin check (Size 0).")
                return False

            # Place order
            return self.open_long_position(shares_validated, trailing_stop_price)

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
            self.ib.qualifyContracts(self.contract)
            logger.info(f"📈 Sending Bracket Order: Buy {shares} @ MKT, Stop @ {stop_price}")
            redis_publisher.log("info", f"📈 Sending order: BUY {shares} shares @ MARKET, Stop Loss @ ${stop_price:.2f}")

            # 1. Parent Order (Entry)
            parent = MarketOrder('BUY', shares)
            parent.transmit = False # <--- DO NOT SEND YET!
            parent.tif = 'DAY'
            
            # 2. Child Order (Stop Loss)
            stop_loss = StopOrder('SELL', shares, stop_price)
            stop_loss.outsideRth = False
            stop_loss.tif = 'DAY'
            stop_loss.transmit = True # <--- This will send the whole package
            parent_trade = self.ib.placeOrder(self.contract, parent)
            stop_loss.parentId = parent_trade.order.orderId
            stop_trade = self.ib.placeOrder(self.contract, stop_loss)
            
            logger.info(f"Orders sent. Parent ID: {parent.orderId}, Stop ParentId: {stop_loss.parentId}")
            redis_publisher.log("info", f"📤 Orders sent - Parent ID: {parent.orderId}")

            # 5. Wait for parent FILL confirmation
            self.ib.sleep(1)
            
            status = parent_trade.orderStatus.status
            
            if status in ['Filled', 'PreSubmitted', 'Submitted']:
                if status == 'Filled':
                    self.entry_price = parent_trade.orderStatus.avgFillPrice
                else:
                    # --- ROBUST PRICE RECOVERY ---
                    # Request market data if not present
                    ticker = self.ib.reqMktData(self.contract, '', False, False)
                    self.ib.sleep(0.5) # Technical time to receive snapshot
                    
                    if ticker and ticker.marketPrice() == ticker.marketPrice(): # Check if NOT NaN
                        self.entry_price = ticker.marketPrice()
                    else:
                        # Final fallback: estimated price to avoid breaking tracking
                        self.entry_price = stop_price + (stop_price * 0.01)
                        logger.warning(f"Ticker not available, using fallback price: {self.entry_price}")
                        redis_publisher.log("warning", f"Ticker not available, using fallback price: {self.entry_price}")

                self.entry_time = datetime.now()
                self.position_size = shares
                self.current_stop_order = stop_loss
                self.stop_price = stop_price
                self.current_position = parent_trade
                
                logger.info(f"✅ Position Tracked. Size: {shares} @ approx ${self.entry_price:.2f}")
                redis_publisher.log("success", f"✅ POSITION OPENED: {shares} shares")
                self.broadcast_position_update()
                return True
            elif status in ['Inactive', 'Cancelled', 'PendingCancel']:
                # Failure (Likely Margin error or other)
                reason = parent_trade.log[-1].message if parent_trade.log else "Unknown reason"
                logger.warning(f"⚠️ Order Rejected: {status}. Reason: {reason}")
                redis_publisher.log("warning", f"⚠️ Order Rejected: {status}. Reason: {reason}")
                
                # --- RETRY LOGIC ---
                # Reduce the size by 10% and retry
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
                logger.info(f"New stop ${new_stop_price:.2f} not better than current ${self.stop_price:.2f}")
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
            
            # Check actual IB position
            actual_position = 0
            for pos in self.ib.positions():
                if pos.contract.symbol == SYMBOL:
                    actual_position = pos.position
                    break
            
            # If IB shows no position but we think we have one, stop was triggered
            if actual_position == 0 and self.position_size > 0:
                logger.info("🔍 Detected position closed - checking stop order status...")
                
                # Try to get fill details from the stop order
                exit_price = self.stop_price  # Default to stop price
                exit_time = datetime.now(ZoneInfo("America/New_York"))
                
                # Look for the filled stop order to get exact exit price
                if self.current_stop_order:
                    for trade in self.ib.trades():
                        if trade.order.orderId == self.current_stop_order.orderId:
                            if trade.orderStatus.status == 'Filled':
                                exit_price = trade.orderStatus.avgFillPrice
                                if trade.fills:
                                    exit_time = trade.fills[-1].time
                                logger.info(f"📋 Stop order filled @ ${exit_price:.2f}")
                            break
                
                # Calculate P&L
                if self.entry_price:
                    pnl = (exit_price - self.entry_price) * self.position_size
                    pnl_percent = (pnl / self.capital) * 100
                else:
                    pnl = 0.0
                    pnl_percent = 0.0
                
                # Log the trade closure
                logger.info(f"🛑 STOP LOSS TRIGGERED @ ${exit_price:.2f}")
                logger.info(f"💰 P&L: ${pnl:.2f} ({pnl_percent:.2f}%)")
                redis_publisher.log("warning", f"🛑 STOP LOSS TRIGGERED @ ${exit_price:.2f} - P&L: ${pnl:.2f}")
                
                # Save trade to database (convert numpy types to native Python)
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
            logger.error(f"Error checking stop loss: {e}")
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

    def validate_order_size(self, contract, intended_shares):
        available_funds = self.get_available_margin()
        safe_funds = available_funds * 0.95
        
        # Use a simpler Market Order for the check
        check_order = MarketOrder('BUY', intended_shares)
        
        try:
            # whatIfOrder returns an OrderState object
            order_state = self.ib.whatIfOrder(contract, check_order)
            
            # --- THE FIX ---
            # Extract initMarginChange safely
            # Sometimes it's on the object directly, sometimes it needs to be cast
            raw_margin = getattr(order_state, 'initMarginChange', "0")
            required_margin = float(raw_margin)
            
            if required_margin > 1e10: # Check for "Infinity" sentinel value
                logger.warning("⚠️ Margin requirement returned as Infinity. Proceeding with caution.")
                return intended_shares 

            logger.info(f"🔎 Margin Check: Required ${required_margin:,.2f} | Available: ${safe_funds:,.2f}")

            if required_margin > safe_funds:
                reduction_ratio = safe_funds / required_margin
                new_size = int(intended_shares * reduction_ratio)
                new_size = max(0, new_size - 1) 
                logger.warning(f"⚠️ Insufficient Margin. Reducing: {intended_shares} -> {new_size}")
                return new_size
            
            return intended_shares

        except Exception as e:
            # If whatIf fails, we fallback to our own calculation rather than returning 0
            logger.error(f"whatIfOrder failed: {e}. Falling back to risk-based size.")
            return intended_shares