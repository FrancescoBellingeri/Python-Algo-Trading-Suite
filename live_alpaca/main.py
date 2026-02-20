from datetime import datetime, time
from zoneinfo import ZoneInfo
import asyncio
import ib_insync
from alpaca.trading.requests import StopOrderRequest, OrderSide, TimeInForce
from src.connector import Connector
from src.data_handler import DataHandler
from src.database import DatabaseHandler
from src.indicator_calculator import IndicatorCalculator
from src.execution_handler import ExecutionHandler
from src.redis_publisher import redis_publisher
from config import SYMBOL, WEBSOCKET_ENABLED, ATR_MULTIPLIER

class TradingBot:
    """Automatic trading bot coordinating all modules."""
    
    def __init__(self):
        """Initialize the trading bot."""
        self.db = DatabaseHandler()
        self.connector = Connector()
        self.data_handler = DataHandler(self.connector, self.db)
        self.indicator_calculator = IndicatorCalculator(self.db)
        self.execution = ExecutionHandler(self.db)
        
        # Bot state
        self.is_running = True
        self.in_position = False
        self.last_signal_time = None
        self.bot_start_time = datetime.now()
        
        # Send initial state to dashboard
        if redis_publisher.enabled:
            redis_publisher.log("info", "🚀 Trading Bot initialized")
    
    async def initialize_components(self):
        """Initialize all system components."""
        try:
            # Connect to IB
            if not await self.connector.connect_to_ib():
                redis_publisher.log("error", "Unable to connect to IB")
                raise Exception("Unable to connect to IB")
            
            if WEBSOCKET_ENABLED and redis_publisher.enabled:
                redis_publisher.log("success", "✅ Dashboard integration active")

            # if not self.execution.update_capital():
            #     logger.error("Capital update failed. Bot stopping for safety.")
            #     redis_publisher.log("error", "Capital update failed")
            #     return False
            
            df = self.data_handler.download_historical_data()
            if df is None or df.empty:
                redis_publisher.log("error", "Data update error")

            self.indicator_calculator.calculate_all(df)

            self.sync_position_state()
            
            redis_publisher.log("success", "✅ All components initialized")

            # --- STARTUP GAP CHECK ---
            # if self.in_position:
            #     try:
            #         current_stop = self.execution.stop_price
            #         if current_stop and not df.empty:
            #              current_price = df.iloc[-1]['close']
            #              if current_price < current_stop:
            #                  logger.warning(f"📉 STARTUP GAP DOWN DETECTED! Price ${current_price:.2f} < Stop ${current_stop:.2f}. Closing immediately.")
            #                  redis_publisher.log("error", f"📉 STARTUP GAP DOWN DETECTED! Price ${current_price:.2f} < Stop ${current_stop:.2f}. Closing.")
                             
            #                  if self.execution.close_position():
            #                      self.in_position = False
            #                      logger.info("✅ Startup gap protection executed.")
            #                      redis_publisher.log("success", "✅ Startup gap protection executed.")
            #     except Exception as e:
            #         logger.error(f"Startup gap check error: {e}")
            
            self.connector._send_account_info()

            return True
        except Exception as e:
            redis_publisher.log("error", f"Initialization error: {str(e)}")
            return False
   
    def sync_position_state(self):
        """
        Synchronizes local state with Alpaca at startup.
        """
        try:
            redis_publisher.log("info", "🔄 Synchronizing positions with Alpaca...")
            
            try:
                position = self.connector.trading_client.get_open_position(SYMBOL)
            except Exception as e:
                # If 404 or specific error, it implies no position.
                # Alpaca raises APIError with code 40410000 if not found.
                if "position does not exist" in str(e):
                    position = None
                else:
                    raise e

            if position:
                self.in_position = True
                self.execution.position_size = float(position.qty)
                self.execution.entry_price = float(position.avg_entry_price)
                redis_publisher.log("warning", f"⚠️ EXISTING POSITION: {self.execution.position_size} shares @ ${self.execution.entry_price:.2f}")

                # Recuperiamo gli ordini aperti per trovare quello di stop
                all_orders = self.connector.trading_client.get_orders()
                # Cerchiamo l'ordine di tipo 'stop' che appartiene a QQQ
                stop_order = next((o for o in all_orders if o.symbol == "QQQ" and o.type == "stop"), None)
                
                if stop_order:
                    self.execution.stop_price = float(stop_order.stop_price)
                    self.execution.current_stop_order = stop_order
                    redis_publisher.log("info", f"Found stop order: {self.execution.stop_price}")
                else:
                    redis_publisher.log("warning", "No stop order found for existing position. Placing new stop loss...")

                    df = self.db.get_latest_data(SYMBOL, 10)
                    last_candle = df.iloc[-1]
                    self.execution.stop_price = round(last_candle['close'] - last_candle['ATR_14'] * ATR_MULTIPLIER, 2)
                    
                    stop_order = StopOrderRequest(
                        symbol=SYMBOL,
                        qty=self.execution.position_size,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC,
                        stop_price=self.execution.stop_price
                    )
                    self.execution.current_stop_order = self.connector.trading_client.submit_order(order_data=stop_order)
                    redis_publisher.log("info", f"Stop order placed: {self.execution.stop_price}")
            
            else:
                self.in_position = False
                self.execution.position_size = 0
                self.execution.entry_price = None
                redis_publisher.log("info", "✅ No open position detected")
        except Exception as e:
            redis_publisher.log("error", f"❌ Synchronization error: {str(e)}")

    def is_market_open(self):
        """Check if market is open."""
        now = datetime.now(ZoneInfo("America/New_York"))
        return time(9, 30) <= now.time() <= time(16, 0) and now.weekday() < 5
    
    def pre_market_routine(self):
        """
        Pre-market routine: update data.
        Run at 9:30 ET.
        """
        redis_publisher.log("info", "🔔 Start pre-market routine")
        
        try:
            # Check if there is an open position from yesterday
            self.sync_position_state()
            
            # 1. Update historical data
            redis_publisher.log("info", "📊 Updating historical data...")

            df = self.data_handler.download_historical_data()
            if df.empty:
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
                        redis_publisher.log("error", f"📉 GAP DOWN DETECTED! Price ${current_price:.2f} < Stop ${current_stop:.2f}. Closing immediately.")
                        
                        if self.execution.close_position():
                            self.in_position = False
                            redis_publisher.log("success", "✅ Gap Down protection executed.")
                        else:
                            redis_publisher.log("error", "❌ Gap Down protection FAILED to close position.")
                        return

                except Exception as e:
                    redis_publisher.log("error", f"Error checking gap down: {e}")

                if self.execution.current_stop_order:
                    redis_publisher.log("success", "✅ Stop Loss already active.")
                    return
                
                # --- RESTORATION LOGIC ---
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
                            redis_publisher.log("warning", f"Stop price unknown. Recalculated: ${target_stop}")
                        else:
                             redis_publisher.log("error", "Stop price unknown and Invalid ATR. Cannot restore stop.")
                             return
                    except Exception as e:
                        redis_publisher.log("error", f"Error calculating fallback stop: {e}")
                        return

                # Restore the stop order
                self.execution.restore_stop_order(target_stop)
            else:
                redis_publisher.log("success", "✅ No open positions. Skipping restore.")
                return
        except Exception as e:
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
            
            redis_publisher.log("debug", f"📊 New 5min candle: {current_time.strftime('%H:%M:%S')}")
            
            # 1. Update data
            df = self.data_handler.update_data(max_retries=10, retry_delay=0.2)
            if df is None or df.empty:
                redis_publisher.log("error", "Candle data update error")
                return
            
            # 2. Calculate indicators (incremental)
            df = self.indicator_calculator.calculate_incremental(df)
            
            # 3. Handle position state and signals
            if not self.in_position:
                # Look for entry signals
                signal = self.execution.check_entry_signals(df)
                if signal:
                    self.in_position = True
            else:
                # We are in position, check what happened
                # A) First check if stop loss was triggered (detects fill of stop order)
                if self.execution.check_stop_loss_triggered():
                    redis_publisher.log("info", "🔄 Position closed by stop loss")
                    self.in_position = False
                
                # B) Then check for strategy exit signals
                elif self.execution.check_exit_signals(df):
                    redis_publisher.log("info", "🔄 Position closed by exit signal")
                    self.in_position = False
                
                # C) Fallback: Check if position was closed externally (manual/other)
                elif not self.execution.has_position():
                    redis_publisher.log("warning", "⚠️ Position closed externally or manually")

                    # Try to fetch real exit price from Alpaca
                    exit_price, exit_time = self.execution.fetch_last_closed_trade_price()

                    if exit_price and self.execution.entry_price and self.execution.position_size:
                        try:
                            capital = self.execution.capital or float(self.execution.trading_client.get_account().cash)
                            pnl = (exit_price - self.execution.entry_price) * self.execution.position_size
                            pnl_percent = (pnl / capital) * 100 if capital else 0.0
                        except Exception:
                            pnl, pnl_percent = 0.0, 0.0

                        redis_publisher.log("warning", f"📋 MANUAL CLOSE @ ${exit_price:.2f} - P&L: ${pnl:.2f} ({pnl_percent:.2f}%)")

                        self.db.save_trade(
                            symbol=SYMBOL,
                            entry_price=float(self.execution.entry_price),
                            exit_price=float(exit_price),
                            quantity=int(self.execution.position_size),
                            entry_time=self.execution.entry_time,
                            exit_time=exit_time,
                            pnl_dollar=float(pnl),
                            pnl_percent=float(pnl_percent),
                            exit_reason="MANUAL"
                        )
                    else:
                        redis_publisher.log("warning", "⚠️ Could not retrieve exit price for manual closure — trade NOT saved to DB.")

                    self.execution.reset_state()
                    self.in_position = False
                
                # D) If still open, update trailing stop
                else:
                    self.execution.update_trailing_stop(df)

            self.connector._send_account_info()
            
        except Exception as e:
            redis_publisher.log("error", f"Error in on_new_candle: {e}")
    
    async def run(self):
        """Main async loop."""
        redis_publisher.log("success", "🚀 Trading Bot started")
        
        if not await self.initialize_components():
            redis_publisher.log("error", "Initialization failed - bot stopped")
            return
                
        # Define target times (New York Time)
        ny_tz = ZoneInfo("America/New_York")
        
        redis_publisher.log("info", "⏳ Bot waiting for hourly triggers...")

        while self.is_running:
            try:
                if not self.connector.is_connected():
                    redis_publisher.log("warning", "IB connection lost - waiting for reconnection...")
                    await asyncio.sleep(5)
                    
                    try:
                        await self.connector.connect_to_ib()
                    except Exception as e:
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

                    # B) 5 Minute Candles (9:35 -> 16:00, every 5 min)
                    elif (time(9, 35) <= now.time() <= time(16, 00)):
                        # Check 5 minute modulo (0, 5, 10, ...)
                        if now.minute % 5 == 0:
                            self.on_new_candle()
                            
                            # Update position data after candle processing if we have a position
                            if self.execution.has_position():
                                try:
                                    df = self.db.get_latest_data(SYMBOL, 2)
                                    if df is not None and not df.empty:
                                        current_sma = df.iloc[-1].get('SMA_200', 0.0)
                                        self.execution.broadcast_position_update(current_ema_value=current_sma)
                                except Exception as e:
                                    redis_publisher.log("error", f"Error broadcasting position update: {e}")
                            
                            if now.hour == 16 and now.minute == 0:
                                redis_publisher.log("info", "🌙 EOD bot is sleeping")
                            
                            await asyncio.sleep(2)

                # 3. Allow IBKR to do whatever it needs to do for 1 second
                await asyncio.sleep(1) 
                
            except KeyboardInterrupt:
                self.is_running = False
                redis_publisher.log("warning", "Bot interrupted by keyboard")
            except Exception as e:
                redis_publisher.log("error", f"Error in main loop: {str(e)}")
                await asyncio.sleep(5)
    
    def shutdown(self):
        """Cleanly shut down the bot."""
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
                redis_publisher.log("warning", "Closing positions before shutdown")
            
            # Disconnect from IB
            if self.connector:
                self.connector.disconnect_from_ib()
                redis_publisher.log("info", "Disconnected from IB")
            
            # Disconnect Redis
            redis_publisher.disconnect()
            
        except Exception as e:
            redis_publisher.log("error", f"Error during shutdown: {e}")
        
        redis_publisher.log("info", "Bot terminated")

if __name__ == "__main__":
    # Needed for ib_insync to coexist with asyncio.run() loop
    ib_insync.util.patchAsyncio()
    bot = TradingBot()
    try:
        # Start async loop
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.shutdown()