import pandas as pd
from ib_insync import Stock, MarketOrder, StopOrder
from src.logger import logger
from src.redis_publisher import redis_publisher
from config import SYMBOL, EXCHANGE, CURRENCY, MAX_RISK_PER_TRADE

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
        self.contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
        self.capital = capital
        self.base_risk = MAX_RISK_PER_TRADE
        
        # Tracking
        self.current_position = None
        self.current_stop_order = None
        self.entry_price = None
        self.stop_price = None
        self.position_size = 0

        self.broadcast_position_update()

        self.atr_multiplier = 10
        
        logger.info(f"ExecutionHandler initialized - Capital: ${capital:,.0f}")
        # Send initial info to dashboard
        redis_publisher.log("info", f"💰 ExecutionHandler initialized - Capital: ${capital:,.0f}")
        redis_publisher.publish("execution-config", {
            "symbol": SYMBOL,
            "capital": capital,
            "risk_per_trade": self.base_risk,
            "atr_multiplier": self.atr_multiplier,
            "leverage": 4
        })
    
    def calculate_position_size(self, entry_price, stop_loss, account_size, risk_per_trade_pct, leverage=4):
        """
        Calculates the number of contracts (or shares) to buy considering:
        - risk per trade in percentage,
        - leverage,
        - maximum absolute loss allowed in dollars.
        """

        # Risk per contract
        R = abs(entry_price - stop_loss)
        if R == 0 or R < 0.01:  # minimal symbolic risk to avoid division by zero
            return 0

        risk_dollars = account_size * risk_per_trade_pct
        risk_based_size = risk_dollars / R
        leverage_based_size = (account_size * leverage) / entry_price
        position_size = int(min(risk_based_size, leverage_based_size))

        return position_size
    
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
                    account_size=self.capital,
                    risk_per_trade_pct=self.base_risk,
                    leverage=4
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
    
    def open_long_position(self, shares, stop_price):
        """
        Opens a long position using a BRACKET ORDER (Parent + Child).
        """
        try:
            logger.info(f"📈 Sending Bracket Order: Buy {shares} @ MKT, Stop @ {stop_price}")
            redis_publisher.log("info", f"📈 Sending order: BUY {shares} shares @ MARKET, Stop Loss @ ${stop_price:.2f}")

            # 1. Parent Order (Entry)
            parent = MarketOrder('BUY', shares)
            parent.transmit = False # <--- DO NOT SEND YET!
            
            # 2. Child Order (Stop Loss)
            stop_loss = StopOrder('SELL', shares, stop_price)
            stop_loss.transmit = True # <--- This will send the whole package
            
            parent_trade = self.ib.placeOrder(self.contract, parent)
            stop_loss.parentId = parent.orderId
            stop_trade = self.ib.placeOrder(self.contract, stop_loss)
            
            logger.info(f"Orders sent. Parent ID: {parent.orderId}, Stop ParentId: {stop_loss.parentId}")
            redis_publisher.log("info", f"📤 Orders sent - Parent ID: {parent.orderId}")

            # 5. Wait for parent FILL confirmation
            self.ib.waitOnUpdate(parent_trade, timeout=10)
            
            if parent_trade.orderStatus.status == 'Filled':
                fill_price = parent_trade.orderStatus.avgFillPrice
                self.entry_price = fill_price
                self.position_size = shares
                
                # Save reference to stop order (which is already active on server!)
                self.current_stop_order = stop_loss
                self.stop_price = stop_price
                self.current_position = parent_trade
                
                logger.info(f"✅ Bracket Executed. Entry: {fill_price}, Active Stop: {stop_price}")
                # Send execution confirmation to dashboard
                redis_publisher.log("success", f"✅ POSITION OPENED: {shares} shares @ ${fill_price:.2f}")
                redis_publisher.publish("order-filled", {
                    "type": "entry",
                    "side": "BUY",
                    "shares": shares,
                    "fill_price": fill_price,
                    "stop_price": stop_price,
                    "order_id": parent.orderId
                })
                
                redis_publisher.publish("position-opened", {
                    "symbol": SYMBOL,
                    "shares": shares,
                    "entry_price": fill_price,
                    "stop_price": stop_price,
                    "risk": (fill_price - stop_price) * shares,
                    "timestamp": pd.Timestamp.now().isoformat()
                })

                self.broadcast_position_update()

                return True
            else:
                logger.warning(f"Entry Order not immediate: {parent_trade.orderStatus.status}")
                redis_publisher.log("warning", f"⚠️ Order not filled: {parent_trade.orderStatus.status}")
                redis_publisher.publish("order-placement", {
                    "status": "failed",
                    "reason": parent_trade.orderStatus.status
                })
                return False

        except Exception as e:
            logger.error(f"Bracket Order Error: {e}")
            redis_publisher.send_error(f"Position opening error: {str(e)}")
            redis_publisher.publish("order-placement", {
                "status": "error",
                "error": str(e)
            })
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
            
            if not self.current_stop_order:
                logger.warning("Open position but no Stop Loss order tracked in memory.")
                redis_publisher.log("warning", "⚠️ Stop loss not found in memory, syncing...")
                self.sync_position_state(df)
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
            
            # Cancel stop loss
            if self.current_stop_order:
                self.ib.cancelOrder(self.current_stop_order)
                redis_publisher.log("info", "❌ Stop loss cancelled")
            
            # Place closing market order
            close_order = MarketOrder('SELL', self.position_size)
            trade = self.ib.placeOrder(self.contract, close_order)
            
            # Wait for execution
            self.ib.waitOnUpdate(trade, timeout=10)
            
            if trade.orderStatus.status == 'Filled':
                exit_price = trade.orderStatus.avgFillPrice
                pnl = (exit_price - self.entry_price) * self.position_size
                
                logger.info(f"✅ Position closed @ ${exit_price:.2f}")
                logger.info(f"💰 P&L: ${pnl:.2f} ({pnl/self.capital*100:.2f}%)")

                # Send trade result to dashboard
                redis_publisher.log("success", f"✅ POSITION CLOSED @ ${exit_price:.2f} - P&L: ${pnl:.2f})")
                
                redis_publisher.publish("position-closed", {
                    "symbol": SYMBOL,
                    "shares": self.position_size,
                    "entry_price": self.entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "result": "WIN" if pnl > 0 else "LOSS"
                })
                
                redis_publisher.publish("trade-result", {
                    "pnl": pnl,
                    "entry": self.entry_price,
                    "exit": exit_price,
                    "shares": self.position_size,
                    "duration_minutes": 0  # You could calculate real duration
                })
                
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

                redis_publisher.publish("capital-update", {
                    "old_capital": old_capital,
                    "new_capital": self.capital,
                    "change": self.capital - old_capital,
                    "currency": "EUR"
                })

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
        
    def sync_position_state(self, df):
        """
        Synchronizes local state with IB at startup.
        """
        try:
            logger.info("🔄 Position state synchronization...")
            redis_publisher.log("info", "🔄 Synchronizing positions with IB...")
            
            redis_publisher.publish("sync-status", {
                "status": "syncing",
                "timestamp": pd.Timestamp.now().isoformat()
            })
            
            # 1. Find position
            positions = self.ib.positions()
            target_pos = None
            for p in positions:
                if p.contract.symbol == SYMBOL and p.position > 0:
                    target_pos = p
                    break
            
            if not target_pos:
                logger.info("No open position on IB.")
                redis_publisher.log("info", "✅ No open position detected")
                
                redis_publisher.publish("sync-status", {
                    "status": "completed",
                    "has_position": False
                })

                self.current_position = None
                self.position_size = 0

                self.broadcast_position_update()
                self.entry_price = None
                self.stop_price = None
                self.current_stop_order = None
                return None
            
            # 2. Update state
            self.position_size = target_pos.position
            self.entry_price = target_pos.avgCost
            logger.info(f"Found position: {self.position_size} shares @ avg ${self.entry_price:.2f}")
            redis_publisher.log("warning", f"⚠️ EXISTING POSITION: {self.position_size} shares @ ${self.entry_price:.2f}")
            
            # 3. Find active stop order
            open_trades = self.ib.openTrades()
            found_stop = False
            
            for trade in open_trades:
                # Trade has both .contract and .order
                if (trade.contract.symbol == SYMBOL and 
                    trade.order.orderType in ['STP', 'TRAIL'] and 
                    trade.order.action == 'SELL'):
                    
                    self.current_stop_order = trade.order  # Save Order, not Trade
                    self.stop_price = trade.order.auxPrice
                    logger.info(f"Found active Stop Loss: ID {trade.order.orderId} @ ${trade.order.auxPrice}")
                    redis_publisher.log("success", f"✅ Active Stop Loss found @ ${trade.order.auxPrice:.2f}")

                    found_stop = True
                    break
            
            if not found_stop:
                logger.warning("⚠️ WARNING: Open position WITHOUT detected Stop Loss!")
                redis_publisher.log("error", "⚠️ WARNING: Position WITHOUT Stop Loss!")
                
                stop_price = self._calculate_emergency_stop(df)

                if stop_price:
                    success = self.place_stop_loss(stop_price)
                    if success:
                        logger.info(f"✅ Emergency Stop Loss placed @ ${stop_price:.2f}")
                        redis_publisher.log("success", f"✅ Emergency Stop Loss activated @ ${stop_price:.2f}")
                    else:
                        logger.error("❌ Emergency Stop Loss placement FAILED!")
                        redis_publisher.send_error("CRITICAL: Unable to place Emergency Stop Loss")
                        
                        redis_publisher.publish("risk-alert", {
                            "type": "stop_loss_failed",
                            "position_size": self.position_size,
                            "entry_price": self.entry_price,
                            "risk": "unlimited"
                        })
                else:
                    logger.error("❌ Unable to calculate stop price!")
                    redis_publisher.send_error("Unable to calculate Stop Loss price")
                    
                    redis_publisher.publish("risk-alert", {
                        "type": "no_stop_loss",
                        "position_size": self.position_size,
                        "entry_price": self.entry_price,
                        "risk": "unlimited"
                    })
            
            # Broadcast initial state
            self.broadcast_position_update()
            
            return {'shares': self.position_size}
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            redis_publisher.send_error(f"Synchronization error: {str(e)}")
            
            redis_publisher.publish("sync-status", {
                "status": "error",
                "error": str(e)
            })
            
            return None
        
    def _calculate_emergency_stop(self, df=None):
        """
        Calculates emergency stop price.
        
        Priority:
        1. If df available, use ATR
        2. Otherwise use fixed percentage from entry price
        
        Args:
            df: DataFrame with market data (optional)
            
        Returns:
            float: Calculated stop price, or None if impossible
        """
        try:
            redis_publisher.log("info", f"DF received - Shape: {df.shape if hasattr(df, 'shape') else 'N/A'}")
            # Method 1: Use ATR if we have data
            if df is not None and len(df) > 0 and 'ATR_14' in df.columns:
                last_candle = df.iloc[-1]
                atr_value = last_candle['ATR_14']
                redis_publisher.log("info", f"ATR_14 value: {atr_value}")
                
                if atr_value > 0:
                    current_price = last_candle['close']
                    risk_per_share = atr_value * self.atr_multiplier
                    stop_price = round(current_price - risk_per_share, 2)
                    
                    logger.info(f"Stop calculated via ATR: ${stop_price:.2f} (ATR: {atr_value:.2f})")
                    return stop_price
            
        except Exception as e:
            logger.error(f"Error calculating emergency stop: {e}")
            return None
        
    def place_stop_loss(self, stop_price):
        """
        Places a standalone Stop Loss order for an existing position.
        Useful to restore stop after the night.
        """
        try:
            # Verify there is a real position on IB
            positions = self.ib.positions()
            current_share_count = 0
            for p in positions:
                if p.contract.symbol == SYMBOL:
                    current_share_count = p.position
                    break
            
            if current_share_count == 0:
                logger.warning("Unable to place Stop Loss: No open position on IB.")
                return False

            # Update internal size if necessary
            self.position_size = current_share_count

            logger.info(f"🛡️ Restoring Stop Loss to ${stop_price:.2f} for {self.position_size} shares")
            redis_publisher.log("info", f"🛡️ Restoring Stop Loss to ${stop_price:.2f}")

            # Create Stop order
            stop_order = StopOrder('SELL', self.position_size, stop_price)
            
            # Send order
            trade = self.ib.placeOrder(self.contract, stop_order)
            
            # Update internal state
            self.current_stop_order = stop_order
            self.stop_price = stop_price
            
            # Log
            logger.info(f"Stop Loss restored successfully. ID: {trade.order.orderId}")
            redis_publisher.log("success", f"✅ Stop Loss reactivated at ${stop_price:.2f}")
            
            redis_publisher.publish("order-placed", {
                "type": "stop_loss_restore",
                "price": stop_price,
                "shares": self.position_size
            })

            return True

        except Exception as e:
            logger.error(f"Error placing Stop Loss: {e}")
            redis_publisher.send_error(f"Stop Loss restore error: {str(e)}")
            return False
        
    def close_all_positions(self):
        """
        EMERGENCY CLOSURE.
        Cancels all pending orders and closes positions at market.
        """
        logger.warning("🚨 TOTAL CLOSURE EXECUTION (PANIC BUTTON) 🚨")
        redis_publisher.log("warning", "🚨 TOTAL CLOSURE STARTED")
        
        try:
            # 1. Cancel all open orders for this symbol
            open_orders = self.ib.openOrders()
            for order in open_orders:
                if order.contract.symbol == SYMBOL:
                    self.ib.cancelOrder(order)
            
            # Wait a moment for cancellations to be processed
            self.ib.sleep(0.5)

            # 2. Get current real position from IB
            positions = self.ib.positions()
            target_pos = None
            
            for p in positions:
                if p.contract.symbol == SYMBOL and p.position != 0:
                    target_pos = p
                    break
            
            if not target_pos:
                logger.info("No position found to close.")
                redis_publisher.log("info", "No position to close.")
                
                # Clean internal variables for safety
                self.current_position = None
                self.current_stop_order = None
                self.position_size = 0

                self.broadcast_position_update()
                return True

            shares_to_close = abs(target_pos.position)
            action = 'SELL' if target_pos.position > 0 else 'BUY' # Handles short too if needed
            
            logger.info(f"Closing {shares_to_close} shares via Market Order...")
            
            # 3. Send Market order
            close_order = MarketOrder(action, shares_to_close)
            trade = self.ib.placeOrder(self.contract, close_order)
            
            self.ib.waitOnUpdate(trade, timeout=10)
            
            if trade.orderStatus.status == 'Filled':
                fill_price = trade.orderStatus.avgFillPrice
                logger.info(f"✅ Position fully liquidated at ${fill_price:.2f}")
                redis_publisher.log("success", f"✅ LIQUIDATION COMPLETED @ ${fill_price:.2f}")
                
                # Total internal state reset
                self.current_position = None
                self.current_stop_order = None
                self.entry_price = None
                self.stop_price = None
                self.position_size = 0

                self.broadcast_position_update()
                
                redis_publisher.publish("position-closed", {
                    "reason": "force_close",
                    "exit_price": fill_price,
                    "shares": shares_to_close
                })
                return True
            else:
                logger.error(f"Liquidation not completed: {trade.orderStatus.status}")
                return False

        except Exception as e:
            logger.error(f"Critical close_all_positions error: {e}")
            redis_publisher.send_error(f"Total closure error: {str(e)}")
            redis_publisher.send_error(f"Total closure error: {str(e)}")
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