from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd
import asyncio
import ib_insync
from ib_insync import StopOrder
from src.ib_connector import IBConnector
from src.data_handler import DataHandler
from src.database import DatabaseHandler
from src.indicator_calculator import IndicatorCalculator
from src.execution_handler import ExecutionHandler
from src.redis_publisher import redis_publisher
from src.logger import logger
import config

class TradingBot:
    """Automatic trading bot coordinating all modules."""
    
    def __init__(self):
        """Initialize the trading bot."""
        self.connector = None
        self.data_handler = None
        self.indicator_calculator = None
        self.execution = None
        self.db = None
        
        # Bot state
        self.is_running = True
        self.in_position = False
        self.last_signal_time = None
        self.bot_start_time = datetime.now()
        
        logger.info("Trading Bot initialized")

        # Send initial state to dashboard
        if redis_publisher.enabled:
            redis_publisher.log("info", "🚀 Trading Bot initialized")
    
    async def initialize_components(self):
        """Initialize all system components."""
        try:
            # Connect to IB
            self.connector = IBConnector()
            if not await self.connector.connect():
                redis_publisher.log("error", "Unable to connect to IB")
                raise Exception("Unable to connect to IB")
            
            if config.WEBSOCKET_ENABLED and redis_publisher.enabled:
                logger.info("✅ Dashboard integration activated")
                redis_publisher.log("success", "Dashboard integration active")
            
            # Initialize modules
            self.data_handler = DataHandler(self.connector)
            self.indicator_calculator = IndicatorCalculator()
            self.execution = ExecutionHandler(self.connector, capital=25000)
            self.db = DatabaseHandler()

            if not self.execution.update_capital():
                logger.error("Capital update failed. Bot stopping for safety.")
                redis_publisher.log("error", "Capital update failed")
                return False
            
            df = self.data_handler.download_historical_data()
            if df is None or df.empty:
                logger.error("Data update error")
                redis_publisher.log("error", "Error retrieving df pre sync")

            position_info = self.sync_position_state()
            if position_info:
                self.in_position = True
                logger.warning(f"⚠️ Bot started with open position of {position_info['shares']} shares")
                redis_publisher.log("warning", f"Bot started with open position: {position_info['shares']} shares")
            else:
                self.in_position = False
                logger.info("✅ Bot started without open positions")
                redis_publisher.log("success", "Bot started without open positions")
            
            logger.info("All components initialized successfully")
            redis_publisher.log("success", "✅ All components initialized")

            # --- STARTUP GAP CHECK ---
            if self.in_position:
                try:
                    current_stop = self.execution.stop_price
                    if current_stop and not df.empty:
                         current_price = df.iloc[-1]['close']
                         if current_price < current_stop:
                             logger.warning(f"📉 STARTUP GAP DOWN DETECTED! Price ${current_price:.2f} < Stop ${current_stop:.2f}. Closing immediately.")
                             redis_publisher.log("error", f"📉 STARTUP GAP DOWN DETECTED! Price ${current_price:.2f} < Stop ${current_stop:.2f}. Closing.")
                             
                             if self.execution.close_position():
                                 self.in_position = False
                                 logger.info("✅ Startup gap protection executed.")
                                 redis_publisher.log("success", "✅ Startup gap protection executed.")
                except Exception as e:
                    logger.error(f"Startup gap check error: {e}")
            
            self.connector._send_account_info()

            return True
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            redis_publisher.log("error", f"Initialization error: {str(e)}")
            return False
   
    def sync_position_state(self):
        """
        Synchronizes local state with IB at startup.
        """
        try:
            logger.info("🔄 Position state synchronization...")
            redis_publisher.log("info", "🔄 Synchronizing positions with IB...")
            
            ib = self.connector.ib

            # CRITICAL FIX: Request ALL open orders from the account (even from previous sessions)
            ib.reqAllOpenOrders() 
            # Give it a moment to populate the local cache
            ib.sleep(1)

            # 1. Find position with retries
            target_pos = None
            stop_order_found = None
            
            # We wait up to 2 seconds (4 x 0.5s) for IB to sync positions and orders
            for i in range(5):
                logger.info(f"Sync attempt {i+1}/5...")
                
                # Update collections
                positions = ib.positions()
                open_trades = ib.openTrades()
                open_orders = ib.openOrders() # Now this should contain everything
                
                # Check for position
                for p in positions:
                    if p.contract.symbol == config.SYMBOL and p.position > 0:
                        target_pos = p
                        break
                
                if target_pos:
                    # Strategy 1: Look in openTrades (Active trades with status)
                    for trade in open_trades:
                        if (trade.contract.symbol == config.SYMBOL and 
                            trade.order.orderType in ['STP', 'TRAIL'] and 
                            trade.order.action == 'SELL'):
                            stop_order_found = trade.order
                            break
                    
                    # Strategy 2: Look in openOrders (Raw orders list)
                    if not stop_order_found:
                        for order in open_orders:
                            if (order.orderType in ['STP', 'TRAIL'] and 
                                order.action == 'SELL'):
                                # Verify symbol if possible, or assume it matches if it's the only active stop
                                # Note: order objects in openOrders might not have full contract info attached directly
                                # so we rely on the order properties
                                stop_order_found = order
                                break

                    # If we found both position and stop, we are good
                    if stop_order_found:
                        logger.info(f"✅ Found orphaned Stop Loss: ID {stop_order_found.orderId} @ ${stop_order_found.auxPrice} (Client {stop_order_found.clientId})")
                
                        # CRITICAL VERIFICATION: Ownership check
                        # If the order belongs to another ClientID (previous session), we cannot modify it.
                        # We must cancel and recreate it to take control.
                        current_client_id = self.connector.ib.client.clientId
                        
                        if stop_order_found.clientId != current_client_id:
                            logger.warning(f"⚠️ Stop Order belongs to old ClientID ({stop_order_found.clientId}). Performing Cancel & Replace to take ownership...")
                            
                            # 1. Try to cancel the old order (ignore errors if already gone)
                            try:
                                ib.cancelOrder(stop_order_found)
                            except Exception:
                                pass
                            
                            # 2. Create a NEW StopOrder (clean)
                            ib.sleep(0.5)  # Wait for cancellation to process
                            
                            new_stop_order = StopOrder('SELL', target_pos.position, float(stop_order_found.auxPrice))
                            new_stop_order.tif = 'GTC'
                            new_stop_order.outsideRth = False
                            
                            # 3. Place new order using SMART routing (not direct NASDAQ)
                            trade = ib.placeOrder(self.execution.contract, new_stop_order)
                            ib.sleep(0.5)
                            
                            # Check if order was accepted
                            if trade.orderStatus.status in ['PreSubmitted', 'Submitted']:
                                self.execution.current_stop_order = trade.order
                                self.execution.stop_price = float(stop_order_found.auxPrice)
                                logger.info(f"✅ Ownership reclaimed. New Stop Order ID: {trade.order.orderId}")
                                redis_publisher.log("success", f"✅ Stop Loss Replaced & Synced @ ${self.execution.stop_price:.2f}")
                                
                                stop_order_found = trade.order
                            else:
                                logger.error(f"❌ Failed to create new stop: {trade.orderStatus.status}")
                                redis_publisher.log("error", f"Failed to create stop during sync")

                        else:
                            # If the ClientID is the same (e.g., fast reconnection same session),
                            # we might be able to modify it, but we reset parentId anyway
                            stop_order_found.parentId = 0
                            self.execution.current_stop_order = stop_order_found
                            self.execution.stop_price = stop_order_found.auxPrice
                            logger.info(f"✅ Resumed control of existing Stop Order ID {stop_order_found.orderId}")
                        break
                else:
                    # If we don't even have a position yet, maybe it's still syncing
                    pass
                
                ib.sleep(0.5)

            if not target_pos:
                logger.info("✅ No open position detected after sync.")
                redis_publisher.log("info", "✅ No open position detected")
                
                self.execution.current_position = None
                self.execution.position_size = 0
                self.execution.entry_price = None
                self.execution.stop_price = None
                self.execution.current_stop_order = None
                self.execution.broadcast_position_update()
                return None
            
            # 2. Update state with found position
            self.in_position = True
            self.execution.position_size = target_pos.position
            self.execution.entry_price = target_pos.avgCost
            logger.info(f"Found position: {self.execution.position_size} shares @ avg ${self.execution.entry_price:.2f}")
            redis_publisher.log("warning", f"⚠️ EXISTING POSITION: {self.execution.position_size} shares @ ${self.execution.entry_price:.2f}")
            
            # 3. Update state with found stop order
            if stop_order_found:
                # CRITICAL: Reset parentId to make it a standalone order
                # This prevents Error 135 when modifying after parent is filled
                stop_order_found.parentId = 0
                self.execution.current_stop_order = stop_order_found
                self.execution.stop_price = stop_order_found.auxPrice
                logger.info(f"✅ Found active Stop Loss: ID {stop_order_found.orderId} @ ${stop_order_found.auxPrice}")
                redis_publisher.log("success", f"✅ Active Stop Loss found @ ${stop_order_found.auxPrice:.2f}")
            else:
                logger.error("❌ CRITICAL: Position found but NO STOP LOSS detected after sync!")
                redis_publisher.log("error", "❌ CRITICAL: Position found but NO STOP LOSS detected after sync!")
                # Optional: You could create a new emergency stop here if needed

            # Broadcast initial state
            self.execution.broadcast_position_update()
            
            return {'shares': self.execution.position_size}
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            redis_publisher.log("error", f"❌ Synchronization error: {str(e)}")
            return None

    def is_market_open(self):
        """Check if market is open."""
        now = datetime.now(ZoneInfo("America/New_York"))
        return time(9, 30) <= now.time() <= time(16, 0) and now.weekday() < 5
    
    def pre_market_routine(self):
        """
        Pre-market routine: update data.
        Run at 9:30 ET.
        """
        logger.info("=" * 50)
        logger.info("START PRE-MARKET ROUTINE")
        logger.info("=" * 50)

        redis_publisher.log("info", "🔔 Start pre-market routine")
        
        try:
            # Check if there is an open position from yesterday
            self.sync_position_state()
            
            # 1. Update historical data
            logger.info("1. Updating historical data...")
            redis_publisher.log("info", "📊 Updating historical data...")

            df = self.data_handler.download_historical_data()
            if df.empty:
                logger.error("Data update error")
                redis_publisher.log("error", "Historical data update error")
                return
            
            self.indicator_calculator.calculate_all(df)

            # --- GAP CHECK LOGIC ---
            if self.in_position:
                # 1. Check for GAP DOWN Trigger (Open Price < Stop Loss)
                try:
                    current_price = df.iloc[-1]['close']
                    current_stop = self.execution.stop_price
                    
                    if current_stop and current_price < current_stop:
                        logger.warning(f"📉 GAP DOWN DETECTED! Price ${current_price:.2f} < Stop ${current_stop:.2f}. Closing immediately.")
                        redis_publisher.log("error", f"📉 GAP DOWN DETECTED! Price ${current_price:.2f} < Stop ${current_stop:.2f}. Closing immediately.")
                        
                        if self.execution.close_position():
                            self.in_position = False
                            logger.info("✅ Gap Down protection executed successfully.")
                            redis_publisher.log("success", "✅ Gap Down protection executed.")
                        else:
                            logger.error("❌ Gap Down protection FAILED to close position.")
                            redis_publisher.log("error", "Gap Down protection FAILED to close position.")
                        return

                except Exception as e:
                    logger.error(f"Error checking gap down: {e}")

                if self.execution.current_stop_order:
                    logger.info("✅ Stop Loss already active. Skipping restore.")
                    redis_publisher.log("success", "✅ Stop Loss already active.")
                    return
                
                # --- RESTORATION LOGIC ---
                logger.warning("⚠️ Position active but Stop Loss MISSING! Attempting restoration...")
                redis_publisher.log("warning", "⚠️ Position active but Stop Loss MISSING! Restoring...")
                
                target_stop = self.execution.stop_price
                
                # If we don't know the stop price (e.g. restart), recalculate it
                if not target_stop or target_stop <= 0:
                    try:
                        current_atr = df.iloc[-1]['ATR_14']
                        current_close = df.iloc[-1]['close']
                        
                        if current_atr > 0:
                            calculated_stop = current_close - (current_atr * self.execution.atr_multiplier)
                            target_stop = round(calculated_stop, 2)
                            logger.warning(f"Stop price unknown. Recalculated using ATR: ${target_stop}")
                            redis_publisher.log("warning", f"Stop price unknown. Recalculated: ${target_stop}")
                        else:
                             logger.error("Stop price unknown and Invalid ATR. Cannot restore stop.")
                             redis_publisher.log("error", "Stop price unknown and Invalid ATR. Cannot restore stop.")
                             return
                    except Exception as e:
                        logger.error(f"Error calculating fallback stop: {e}")
                        return

                # Restore the stop order
                self.execution.restore_stop_order(target_stop)
            else:
                logger.info("✅ No open positions. Skipping restore.")
                redis_publisher.log("success", "✅ No open positions. Skipping restore.")
                return
        except Exception as e:
            logger.error(f"Error in pre-market routine: {e}")
            redis_publisher.log("error", f"Pre-market routine error: {str(e)}")
    
    def on_new_candle(self):
        """
        Callback executed every 5 minutes during trading.
        """
        try:
            current_time = datetime.now(ZoneInfo("America/New_York"))
            
            # Check we are in trading hours (9:35 - 15:55 NY time)
            if not self.is_market_open():
                return
            
            logger.info(f"📊 New 5min candle: {current_time.strftime('%H:%M:%S')}")
            redis_publisher.log("debug", f"📊 New 5min candle: {current_time.strftime('%H:%M:%S')}")
            
            # 1. Update data
            df = self.data_handler.update_data(max_retries=10, retry_delay=0.2)
            if df is None or df.empty:
                logger.error("Data update error")
                redis_publisher.log("error", "Candle data update error")
                return
            
            # 2. Calculate indicators (incremental)
            df = self.indicator_calculator.calculate_incremental(df)
            
            # 3. Check signals
            if not self.in_position:
                signal = self.execution.check_entry_signals(df)
                if signal:
                    self.in_position = True
            else:
                # First check if stop loss was triggered
                if self.execution.check_stop_loss_triggered():
                    logger.info("🔄 Position closed by stop loss - resetting state")
                    redis_publisher.log("info", "🔄 Position closed by stop loss")
                    self.in_position = False
                elif self.execution.check_exit_signals(df):
                    logger.info("🔄 Position closed by exit signal - resetting state")
                    redis_publisher.log("info", "🔄 Position closed by exit signal")
                    self.in_position = False
                else:
                    # Position still open - update trailing stop
                    self.execution.update_trailing_stop(df)

            self.connector._send_account_info()
            
        except Exception as e:
            logger.error(f"Error in on_new_candle: {e}")
            redis_publisher.log("error", f"Candle processing error: {str(e)}")
    
    async def run(self):
        """Main async loop."""
        logger.info("Trading Bot started")
        redis_publisher.log("success", "🚀 Trading Bot started")
        
        if not await self.initialize_components():
            redis_publisher.log("error", "Initialization failed - bot stopped")
            return
                
        # Define target times (New York Time)
        ny_tz = ZoneInfo("America/New_York")
        
        logger.info("⏳ Waiting for hourly triggers...")
        redis_publisher.log("info", "⏳ Bot waiting for hourly triggers...")

        while self.is_running:
            try:
                if not self.connector.is_connected():
                    logger.warning("IB connection lost - waiting for reconnection...")
                    redis_publisher.log("warning", "IB connection lost - waiting for reconnection...")
                    await asyncio.sleep(5)
                    
                    try:
                        await self.connector.connect()
                    except Exception as e:
                        logger.error(f"Reconnect failed: {e}")
                        redis_publisher.log("error", f"Reconnect failed: {e}")
                        continue
                
                # 1. Get current NY time
                now = datetime.now(ny_tz)
                
                # 2. Check SECOND 00 (Trigger at start of minute)
                if now.second == 0:
                    
                    # A) Pre-Market Routine (09:30)
                    if now.hour == 9 and now.minute == 30:
                        self.pre_market_routine()
                        await asyncio.sleep(2)

                    # B) EOD Routine (16:00)
                    elif now.hour == 16 and now.minute == 0:
                        redis_publisher.log("info", "🌙 EOD bot is sleeping")
                        await asyncio.sleep(2)

                    # C) 5 Minute Candles (9:35 -> 15:55, every 5 min)
                    elif (time(9, 35) <= now.time() <= time(15, 55)):
                        # Check 5 minute modulo (0, 5, 10, ...)
                        if now.minute % 5 == 0:
                            self.on_new_candle()
                            
                            # Update position data after candle processing if we have a position
                            if self.execution.has_position():
                                try:
                                    df = self.db.get_latest_data(config.SYMBOL, 2)
                                    if df is not None and not df.empty:
                                        current_sma = df.iloc[-1].get('SMA_200', 0.0)
                                        self.execution.broadcast_position_update(current_ema_value=current_sma)
                                except Exception as e:
                                    logger.error(f"Error broadcasting position update: {e}")
                            
                            await asyncio.sleep(2)

                # 3. Allow IBKR to do whatever it needs to do for 1 second
                await asyncio.sleep(1) 
                
            except KeyboardInterrupt:
                self.is_running = False
                redis_publisher.log("warning", "Bot interrupted by keyboard")
            except Exception as e:
                logger.error(f"Error in loop: {e}")
                redis_publisher.log("error", f"Error in main loop: {str(e)}")
                await asyncio.sleep(5)
    
    def shutdown(self):
        """Cleanly shut down the bot."""
        logger.info("Bot shutdown...")
        redis_publisher.log("warning", "🛑 Bot shutdown in progress...")
        
        try:
            # Send final status
            redis_publisher.publish("bot-status", {
                "status": "stopped",
                "timestamp": datetime.now().isoformat(),
                "reason": "shutdown"
            })
            
            # Close positions if necessary
            if self.execution and self.execution.has_position():
                logger.warning("Closing open positions...")
                redis_publisher.log("warning", "Closing positions before shutdown")
            
            # Disconnect from IB
            if self.connector:
                self.connector.disconnect()
                redis_publisher.log("info", "Disconnected from IB")
            
            # Disconnect Redis
            redis_publisher.disconnect()
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        
        logger.info("Bot terminated")

if __name__ == "__main__":
    # Needed for ib_insync to coexist with asyncio.run() loop
    ib_insync.util.patchAsyncio()
    bot = TradingBot()
    try:
        # Start async loop
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.shutdown()