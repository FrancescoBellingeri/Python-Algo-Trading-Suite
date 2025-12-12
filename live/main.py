from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd
import asyncio
import ib_insync
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
                redis_publisher.send_error("Unable to connect to IB")
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
                redis_publisher.send_error("Capital update failed")
                return False
            
            df = self.data_handler.download_historical_data()
            if df is None or df.empty:
                logger.error("Data update error")
                redis_publisher.send_error("Error retrieving df pre sync")

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

            self.connector._send_account_info()

            return True
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            redis_publisher.send_error(f"Initialization error: {str(e)}")
            return False
   
    def sync_position_state(self):
        """
        Synchronizes local state with IB at startup.
        """
        try:
            logger.info("🔄 Position state synchronization...")
            redis_publisher.log("info", "🔄 Synchronizing positions with IB...")
            
            ib = self.connector.ib

            # 1. Find position
            positions = ib.positions()
            target_pos = None
            for p in positions:
                if p.contract.symbol == config.SYMBOL and p.position > 0:
                    target_pos = p
                    break
            
            if not target_pos:
                logger.info("No open position on IB.")
                redis_publisher.log("info", "✅ No open position detected")

                self.current_position = None
                self.position_size = 0
                self.entry_price = None
                self.stop_price = None
                self.current_stop_order = None

                self.execution.broadcast_position_update()
                return None
            
            # 2. Update state
            self.in_position = True
            self.execution.position_size = target_pos.position
            self.execution.entry_price = target_pos.avgCost
            logger.info(f"Found position: {self.execution.position_size} shares @ avg ${self.execution.entry_price:.2f}")
            redis_publisher.log("warning", f"⚠️ EXISTING POSITION: {self.execution.position_size} shares @ ${self.execution.entry_price:.2f}")
            
            # 3. Find active stop order
            open_trades = ib.openTrades()
            
            for trade in open_trades:
                # Trade has both .contract and .order
                if (trade.contract.symbol == config.SYMBOL and 
                    trade.order.orderType in ['STP', 'TRAIL'] and 
                    trade.order.action == 'SELL'):
                    
                    self.execution.current_stop_order = trade.order  # Save Order, not Trade
                    self.execution.stop_price = trade.order.auxPrice
                    logger.info(f"Found active Stop Loss: ID {trade.order.orderId} @ ${trade.order.auxPrice}")
                    redis_publisher.log("success", f"✅ Active Stop Loss found @ ${trade.order.auxPrice:.2f}")

                    break

            # Broadcast initial state
            self.execution.broadcast_position_update()
            
            return {'shares': self.execution.position_size}
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            redis_publisher.send_error(f"Synchronization error: {str(e)}")
            
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
                redis_publisher.send_error("Historical data update error")
                return
            
            self.indicator_calculator.calculate_all(df)

            # --- GAP CHECK LOGIC ---
            if self.in_position:
                if self.execution.current_stop_order:
                    logger.info("✅ Stop Loss already active. Skipping restore.")
                    redis_publisher.log("success", "✅ Stop Loss already active. Skipping restore.")
                    return
                else:
                    logger.error("Open position found but no Stop Loss active. Skipping restore.")
                    redis_publisher.send_error("Open position found but no Stop Loss active. Skipping restore.")
                    return
            else:
                logger.info("✅ No open positions. Skipping restore.")
                redis_publisher.log("success", "✅ No open positions. Skipping restore.")
                return
        except Exception as e:
            logger.error(f"Error in pre-market routine: {e}")
            redis_publisher.send_error(f"Pre-market routine error: {str(e)}")
    
    def on_new_candle(self):
        """
        Callback executed every 5 minutes during trading.
        """
        try:
            current_time = datetime.now(ZoneInfo("America/New_York"))
            
            # Check we are in trading hours (9:35 - 15:55 NY time)
            if not self.is_market_open():
                return
            
            logger.info(f"[{current_time.strftime('%H:%M:%S')}] Processing new 5 minute candle...")
            redis_publisher.log("debug", f"📊 New 5min candle: {current_time.strftime('%H:%M:%S')}")
            
            # 1. Update data
            df = self.data_handler.update_data(max_retries=10, retry_delay=0.2)
            if df is None or df.empty:
                logger.error("Data update error")
                redis_publisher.send_error("Candle data update error")
                return
            
            # 2. Calculate indicators (incremental)
            df = self.indicator_calculator.calculate_incremental(df)
            
            # 3. Check signals (commented in your original code)
            if not self.in_position:
                signal = self.execution.check_entry_signals(df)
                if signal:
                    self.in_position = True
            else:
                if self.execution.check_exit_signals(df):
                    logger.info("Trade closed because conditions no longer met")
                    self.in_position = False
                else:
                    self.execution.update_trailing_stop(df)

            self.connector._send_account_info()
            
        except Exception as e:
            logger.error(f"Error in on_new_candle: {e}")
            redis_publisher.send_error(f"Candle processing error: {str(e)}")
    
    async def run(self):
        """Main async loop."""
        logger.info("Trading Bot started")
        redis_publisher.log("success", "🚀 Trading Bot started")
        
        if not await self.initialize_components():
            redis_publisher.send_error("Initialization failed - bot stopped")
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
                        redis_publisher.send_error(f"Reconnect failed: {e}")
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
                redis_publisher.send_error(f"Error in main loop: {str(e)}")
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