from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd
import asyncio
import ib_insync
import os
import json
from src.ib_connector import IBConnector
from src.data_handler import DataHandler
from src.database import DatabaseHandler
from src.indicator_calculator import IndicatorCalculator
from src.execution_handler import ExecutionHandler
from src.ib_dashboard_handler import IBDashboardHandler
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

        self.account_id = None
        self.pnl_stream = None
        
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
                self.dashboard_handler = IBDashboardHandler(self.connector.ib)
                
                # Setup callback for dashboard commands
                redis_publisher.set_command_callback(self.handle_dashboard_command)
                
                logger.info("✅ Dashboard integration activated")
                redis_publisher.log("success", "Dashboard integration active")
            
            # Initialize modules
            self.data_handler = DataHandler(self.connector)
            self.indicator_calculator = IndicatorCalculator()
            self.execution = ExecutionHandler(self.connector, capital=25000)
            self.db = DatabaseHandler()

            self.account_id = self.connector.ib.managedAccounts()[0]
            self.pnl_stream = self.connector.ib.reqPnL(self.account_id)

            if not self.execution.update_capital():
                logger.error("Capital update failed. Bot stopping for safety.")
                redis_publisher.send_error("Capital update failed")
                return False
            
            # Send capital to dashboard
            capital_info = {
                "available_capital": self.execution.capital,
                "max_risk_per_trade": self.execution.base_risk,
                "position_size_limit": self.execution.capital * self.execution.base_risk
            }
            redis_publisher.publish("capital-update", capital_info)
            
            df = self.data_handler.download_historical_data()
            if df is None or df.empty:
                logger.error("Data update error")
                redis_publisher.send_error("Error retrieving df pre sync")

            position_info = self.execution.sync_position_state(df)
            if position_info:
                self.in_position = True
                logger.warning(f"⚠️ Bot started with open position of {position_info['shares']} shares")
                redis_publisher.log("warning", f"Bot started with open position: {position_info['shares']} shares")

                # Send position info to dashboard
                redis_publisher.publish("position-status", {
                    "has_position": True,
                    "shares": position_info['shares'],
                    "entry_price": position_info.get('avg_price', 0)
                })
            else:
                self.in_position = False
                logger.info("✅ Bot started without open positions")
                redis_publisher.log("success", "Bot started without open positions")
                redis_publisher.publish("position-status", {"has_position": False})
            
            logger.info("All components initialized successfully")
            redis_publisher.log("success", "✅ All components initialized")

            # Send system status to dashboard
            self.send_system_status()

            return True
            
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            redis_publisher.send_error(f"Initialization error: {str(e)}")
            return False
    
    def send_system_status(self):
        """Send complete system status to dashboard."""
        status = {
            "bot_status": "running" if self.is_running else "stopped",
            "connection_status": "connected" if self.connector else "disconnected",
            "in_position": self.in_position,
            "market_hours": self.is_market_open(),
            "uptime_seconds": (datetime.now() - self.bot_start_time).total_seconds(),
            "config": {
                "symbol": config.SYMBOL,
                "exchange": config.EXCHANGE,
                "max_risk": config.MAX_RISK_PER_TRADE,
                "paper_trading": config.IB_PORT == 7497
            }
        }
        redis_publisher.publish("system-status", status)
    
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
        redis_publisher.publish("market-event", {"type": "pre-market", "time": datetime.now().isoformat()})
        
        try:
            # Check if there is an open position from yesterday
            self.get_open_positions()
            
            # 1. Update historical data
            logger.info("1. Updating historical data...")
            redis_publisher.log("info", "📊 Updating historical data...")

            df = self.data_handler.download_historical_data()
            if df.empty:
                logger.error("Data update error")
                redis_publisher.send_error("Historical data update error")
                return
            
            self.indicator_calculator.calculate_all(df)

            # Send latest indicators to dashboard
            if not df.empty:
                last_row = df.iloc[-1]
                indicators = {
                    "rsi": float(last_row.get('RSI', 0)),
                    "macd": float(last_row.get('MACD', 0)),
                    "macd_signal": float(last_row.get('MACD_signal', 0)),
                    "bb_upper": float(last_row.get('BB_Upper', 0)),
                    "bb_lower": float(last_row.get('BB_Lower', 0)),
                    "sma_20": float(last_row.get('SMA_20', 0)),
                    "sma_50": float(last_row.get('SMA_50', 0)),
                    "volume": float(last_row.get('Volume', 0)),
                    "close": float(last_row.get('Close', 0))
                }
                redis_publisher.publish("indicators-update", indicators)
                redis_publisher.log("success", "✅ Indicators calculated and updated")

            # --- GAP CHECK LOGIC ---
            if self.in_position:
                last_sl_price = self.load_overnight_state()
                
                if last_sl_price:
                    logger.info(f"🔍 Gap Check: SL saved yesterday = {last_sl_price}")
                    
                    # Get current price. 
                    # Option A: Last close (if pre-market) or Today's open if data is live
                    # For safety, ask for instant live price
                    tickers = self.connector.ib.reqTickers(self.connector.contract)
                    if tickers:
                        current_price = tickers[0].marketPrice() # Current price (Last/Mark)
                        # If marketPrice is not available (e.g. delayed data), use last close from df
                        if pd.isna(current_price) or current_price == 0:
                             current_price = df.iloc[-1]['Close']
                    else:
                        current_price = df.iloc[-1]['Close']

                    logger.info(f"Estimated Open Price: {current_price}")

                    # CONDITION: If current price is LOWER than old stop loss
                    if current_price < last_sl_price:
                        logger.warning(f"🚨 GAP DOWN DETECTED! Open ({current_price}) < Old SL ({last_sl_price})")
                        redis_publisher.log("error", f"🚨 GAP DOWN: {current_price} < {last_sl_price}. Immediate close!")
                        
                        # Close position immediately (Market Order)
                        self.execution.close_all_positions() 
                        self.in_position = False
                        self.clear_overnight_state()
                        return # Exit, trade finished
                    
                    else:
                        logger.info("✅ Price above old SL. Position remains open.")
                        redis_publisher.log("success", "✅ No critical Gap. Position maintained.")
                        
                        self.execution.place_stop_loss(last_sl_price) 
                else:
                    logger.info("No saved SL state found.")
            
        except Exception as e:
            logger.error(f"Error in pre-market routine: {e}")
            redis_publisher.send_error(f"Pre-market routine error: {str(e)}")
    
    def get_open_positions(self):
        """
        Retrieve all open positions
        """
        try:
            # Method 1: Current positions
            positions = self.connector.ib.positions()
            
            logger.info(f"Found {len(positions)} open positions")
            redis_publisher.log("info", f"📈 Found {len(positions)} open positions")

            if len(positions) > 0:
                self.in_position = True

                # Send position details to dashboard
                for pos in positions:
                    if pos.contract.symbol == config.SYMBOL:
                        pos_info = {
                            "symbol": pos.contract.symbol,
                            "shares": pos.position,
                            "avg_cost": pos.avgCost
                        }
                        redis_publisher.publish("position-info", pos_info)
            
        except Exception as e:
            logger.error(f"Error retrieving positions: {e}")
            redis_publisher.send_error(f"Position retrieval error: {str(e)}")
    
    def end_of_day_routine(self):
        """
        End of day routine.
        At 16:00: Cancel SL, keep position, save SL level.
        """
        logger.info("=" * 50)
        logger.info("END OF DAY ROUTINE - OVERNIGHT PREPARATION")
        logger.info("=" * 50)
        
        redis_publisher.log("info", "🔔 Start end of day routine (Overnight Mode)")
        
        try:
            # Update position info
            self.get_open_positions()

            if self.in_position:
                logger.info("Open position detected. Searching for active Stop Loss...")
                redis_publisher.log("info", "Open position detected. Searching for active Stop Loss...")
                
                # 1. Find active Stop Loss order on IB
                # Note: ib.openOrders() returns all open orders
                open_trades = self.connector.ib.openTrades()
                stop_order = None
                
                for trade in open_trades:
                    # Search for STP (Stop) or TRAIL (Trailing Stop) orders
                    if trade.order.orderType in ['STP', 'TRAIL', 'STP LMT']:
                        stop_order = trade.order
                        break
                
                if stop_order:
                    # 2. Get stop price (auxPrice)
                    # For Trailing sometimes need to calculate, but auxPrice is base trigger for STP
                    current_sl_price = stop_order.auxPrice
                    
                    if current_sl_price and current_sl_price > 0:
                        logger.info(f"Found active Stop Loss at: {current_sl_price}")
                        redis_publisher.log("info", f"Found active Stop Loss at: {current_sl_price}")
                        
                        # 3. Save state to file
                        self.save_overnight_state(current_sl_price)
                        
                        # 4. Cancel Stop Loss order on IB
                        self.connector.ib.cancelOrder(stop_order)
                        self.execution.current_stop_order = None
                        self.execution.stop_price = None
                        logger.info("❌ Stop Loss order cancelled for the night.")
                        redis_publisher.log("warning", f"🌙 SL cancelled at {current_sl_price} (Overnight Save)")
                    else:
                        logger.warning("Stop order found but invalid price.")
                else:
                    logger.info("No Stop Loss order found to cancel.")
                    redis_publisher.log("info", "No Stop Loss order found to cancel.")
            else:
                logger.info("No open position. No action needed.")
                redis_publisher.log("info", "No open position. No action needed.")
                self.clear_overnight_state()

            redis_publisher.publish("market-event", {"type": "market-close", "time": datetime.now().isoformat()})
            
        except Exception as e:
            redis_publisher.send_error(f"EOD routine error: {str(e)}")
            logger.error(f"Error in EOD routine: {e}")
    
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

            # Send latest values to dashboard
            if not df.empty:
                last_row = df.iloc[-1]

                candle_data = {
                    "time": current_time.isoformat(),
                    "open": float(last_row.get('Open', 0)),
                    "high": float(last_row.get('High', 0)),
                    "low": float(last_row.get('Low', 0)),
                    "close": float(last_row.get('Close', 0)),
                    "volume": float(last_row.get('Volume', 0)),
                    "rsi": float(last_row.get('RSI', 0)),
                    "macd": float(last_row.get('MACD', 0)),
                    "macd_signal": float(last_row.get('MACD_signal', 0))
                }
                redis_publisher.publish("candle-update", candle_data)
            
            # 3. Check signals (commented in your original code)
            if not self.in_position:
                signal = self.execution.check_entry_signals(df)
                if signal:
                    redis_publisher.send_trade_signal("BUY", {
                        "reason": "Entry signal detected",
                        "indicators": candle_data
                    })
                    self.in_position = True
            else:
                if self.execution.check_exit_signals(df):
                    redis_publisher.send_trade_signal("SELL", {
                        "reason": "Exit signal detected",
                        "indicators": candle_data
                    })
                    logger.info("Trade closed because conditions no longer met")
                    self.in_position = False
            
                self.execution.update_trailing_stop(df)
            
            # 4. Update system status
            self.send_system_status()
            
        except Exception as e:
            logger.error(f"Error in on_new_candle: {e}")
            redis_publisher.send_error(f"Candle processing error: {str(e)}")

    def handle_dashboard_command(self, command: dict):
        """
        Handle commands received from dashboard via Redis.
        """
        cmd_type = command.get("type")
        payload = command.get("payload", {})
        
        logger.info(f"📥 Command received from dashboard: {cmd_type}")
        redis_publisher.log("info", f"Command received: {cmd_type}")
        
        try:
            if cmd_type == "stop":
                self.handle_stop_command()
                
            elif cmd_type == "pause":
                self.handle_pause_command()
                
            elif cmd_type == "resume":
                self.handle_resume_command()
                
            elif cmd_type == "status":
                self.send_system_status()
                if self.dashboard_handler:
                    self.dashboard_handler._send_initial_state()
                    
            elif cmd_type == "close_positions":
                self.handle_close_positions()
                
            elif cmd_type == "cancel_orders":
                self.handle_cancel_orders()
                
            elif cmd_type == "update_risk":
                new_risk = payload.get("max_risk")
                if new_risk:
                    config.MAX_RISK_PER_TRADE = new_risk
                    redis_publisher.log("info", f"Risk limit updated to {new_risk}")
                    
            elif cmd_type == "force_update":
                # Force data update
                self.force_data_update()
                
            else:
                logger.warning(f"Command not recognized: {cmd_type}")
                redis_publisher.log("warning", f"Command not recognized: {cmd_type}")
                
        except Exception as e:
            logger.error(f"Error handling command {cmd_type}: {e}")
            redis_publisher.send_error(f"Command execution error: {str(e)}")
    
    def handle_stop_command(self):
        """Handle stop command."""
        logger.warning("⛔ STOP command received - Shutting down bot")
        redis_publisher.log("warning", "⛔ Bot stopped by dashboard command")
        self.is_running = False
        
        # Close positions if requested
        if self.in_position:
            redis_publisher.log("warning", "Closing positions before shutdown...")
            # self.execution.close_all_positions()
    
    def handle_pause_command(self):
        """Handle pause command."""
        logger.info("⏸️ PAUSE command received")
        redis_publisher.log("info", "⏸️ Bot paused")
        self.is_running = False
        redis_publisher.publish("bot-status", {"status": "paused"})
    
    def handle_resume_command(self):
        """Handle resume command."""
        logger.info("▶️ RESUME command received")
        redis_publisher.log("info", "▶️ Bot resumed")
        self.is_running = True
        redis_publisher.publish("bot-status", {"status": "running"})
    
    def handle_close_positions(self):
        """Close all open positions."""
        logger.warning("Position closure requested by dashboard")
        redis_publisher.log("warning", "📉 Position closure from dashboard")
        
        if self.execution and self.execution.has_position():
            # self.execution.close_all_positions()
            self.in_position = False
            redis_publisher.publish("position-status", {"has_position": False})
        else:
            redis_publisher.log("info", "No positions to close")
    
    def handle_cancel_orders(self):
        """Cancel all open orders."""
        logger.warning("Order cancellation requested by dashboard")
        redis_publisher.log("warning", "❌ Order cancellation from dashboard")
        
        if self.connector:
            self.connector.ib.reqGlobalCancel()
            redis_publisher.log("success", "All orders cancelled")
    
    def force_data_update(self):
        """Force immediate data update."""
        logger.info("Data update forced by dashboard")
        redis_publisher.log("info", "🔄 Data update forced")
        
        try:
            df = self.data_handler.update_data()
            if df is not None and not df.empty:
                df = self.indicator_calculator.calculate_incremental(df)
                redis_publisher.log("success", "✅ Data updated successfully")
                
                # Send latest data
                last_row = df.iloc[-1]
                candle_data = {
                    "time": datetime.now().isoformat(),
                    "close": float(last_row.get('Close', 0)),
                    "volume": float(last_row.get('Volume', 0)),
                    "rsi": float(last_row.get('RSI', 0))
                }
                redis_publisher.publish("data-update", candle_data)
        except Exception as e:
            redis_publisher.send_error(f"Forced update error: {str(e)}")

    async def monitor_pnl_task(self):
        """
        Background task sending PnL to Redis every second.
        Does not block trading because uses asyncio.sleep.
        """
        logger.info("Starting PnL monitoring in background...")
        
        while self.is_running:
            try:
                # Read values from pnl_stream object that IB updates in real-time
                if self.pnl_stream:
                    pnl_data = {
                        "account": self.account_id,
                        "dailyPnL": self.pnl_stream.dailyPnL,      # Daily PnL
                        "unrealizedPnL": self.pnl_stream.unrealizedPnL, # Unrealized PnL (open positions)
                        "realizedPnL": self.pnl_stream.realizedPnL,     # Realized PnL
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    # Publish to dedicated channel for WebSocket server
                    redis_publisher.publish("pnl-update", pnl_data)
                
                # Wait 1 second before next send (to not clog Redis)
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error in PnL monitoring: {e}")
                await asyncio.sleep(5) # Longer wait in case of error

    def save_overnight_state(self, stop_loss_price):
        """Save stop loss to file for next morning."""
        state = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "last_stop_loss": float(stop_loss_price),
            "symbol": config.SYMBOL
        }
        with open("bot_state.json", "w") as f:
            json.dump(state, f)
        logger.info(f"💾 Overnight state saved: SL at {stop_loss_price}")

    def load_overnight_state(self):
        """Load saved stop loss."""
        if not os.path.exists("bot_state.json"):
            return None
        
        try:
            with open("bot_state.json", "r") as f:
                state = json.load(f)
            
            # Check data is recent (yesterday or today)
            # Here we simplify returning only the value
            return state.get("last_stop_loss")
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            return None
        
    def clear_overnight_state(self):
        """Delete state file."""
        if os.path.exists("bot_state.json"):
            os.remove("bot_state.json")

    async def run(self):
        """Main async loop."""
        logger.info("Trading Bot started")
        redis_publisher.log("success", "🚀 Trading Bot started")
        
        if not await self.initialize_components():
            redis_publisher.send_error("Initialization failed - bot stopped")
            return
        
        asyncio.create_task(self.monitor_pnl_task())
        
        # Define target times (New York Time)
        ny_tz = ZoneInfo("America/New_York")
        
        logger.info("⏳ Waiting for hourly triggers...")
        redis_publisher.log("info", "⏳ Bot waiting for hourly triggers...")

        while self.is_running:
            try:
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
                        self.end_of_day_routine()
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
                # self.execution.close_all_positions()
            
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