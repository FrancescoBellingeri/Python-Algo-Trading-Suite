import pandas as pd
import os
from ib_insync import Stock, util
from src.logger import logger
from config import SYMBOL, EXCHANGE, CURRENCY
from src.database import DatabaseHandler
from src.redis_publisher import redis_publisher
import time
from datetime import datetime, timedelta
import pytz

class DataHandler:
    """Handles market data download and update."""
    
    def __init__(self, ib_connector):
        """
        Initializes DataHandler.
        
        Args:
            ib_connector: Already connected IBConnector instance
        """
        self.ib = ib_connector.ib
        self.db = DatabaseHandler()
        self.symbol = SYMBOL
        self.contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
        
        # Path to save data
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        self.data_file = os.path.join(self.data_dir, f'{SYMBOL}_5min.csv')

        # Send initial info
        redis_publisher.publish("data-config", {
            "symbol": SYMBOL,
            "exchange": EXCHANGE,
            "currency": CURRENCY,
            "timeframe": "5min",
            "data_file": self.data_file
        })
        
    def download_historical_data(self):
        """
        Downloads last 1000 minutes to calculate all indicators.
        """
        try:            
            logger.info(f"Downloading 1000 minutes of historical data for {self.symbol}...")
            redis_publisher.log("success", f"Downloading 1000 minutes of historical data for {self.symbol}...")

            bars = self.ib.reqHistoricalData(
                self.contract,
                endDateTime='',
                durationStr='5 D',
                barSizeSetting='5 mins',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1
            )
            
            # Convert to DataFrame
            if bars:
                df = util.df(bars)
                df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
                df = df.sort_values('date').reset_index(drop=True)
                
                # Save to file
                df.to_csv(self.data_file, index=False)

                # Save to DB
                success = self.db.save_candles(df, self.symbol)
                if success:
                    logger.info(f"✅ Downloaded and saved {len(df)} candles to Database.")
                    redis_publisher.log("success", f"✅ Downloaded and saved {len(df)} candles to Database.")

                # Send download statistics
                redis_publisher.publish("data-download", {
                    "status": "completed",
                    "symbol": self.symbol,
                    "candles_count": len(df),
                    "start_date": str(df['date'].min()),
                    "end_date": str(df['date'].max()),
                    "saved_to_db": success
                })

                logger.info(f"Downloaded and saved {len(df)} candles. From {df['date'].min()} to {df['date'].max()}")
                redis_publisher.log("success", f"Downloaded and saved {len(df)} candles. From {df['date'].min()} to {df['date'].max()}")
                return df
            
            logger.info(f"No data downloaded")
            redis_publisher.log("success", f"No data downloaded")

            return pd.DataFrame
        except Exception as e:
            logger.error(f"Error downloading historical data: {e}")
            redis_publisher.send_error(f"Error downloading historical data: {str(e)}")
            return pd.DataFrame()
    
    def update_data(self, max_retries=10, retry_delay=0.2):
        """
        Updates data with the last 5-minute candle.
        To be executed every day every 5 minutes.
        """
        try:
            # Load existing data
            # if not os.path.exists(self.data_file):
            #     logger.error(f"Data file not found: {self.data_file}")
            #     logger.info("Run download_historical_data() first")
            #     return False
            
            # df = pd.read_csv(self.data_file)
            # df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
            
            # --- STEP 1: Calculate what SHOULD be the last candle ---
            ny_tz = pytz.timezone('America/New_York')
            now = datetime.now(ny_tz)

            # Round "now" to previous 5 minutes
            # Ex. 10:03:45 -> 10:00:00
            current_interval = now.replace(second=0, microsecond=0) 
            current_interval = current_interval - timedelta(minutes=now.minute % 5)

            # The last CLOSED candle is the one finished 5 minutes ago
            # Ex. If we are in the 10:00 interval, the last complete candle is the 09:55 one
            expected_candle_time = current_interval - timedelta(minutes=5)
            
            # 1. Use limit=1 to fetch only the last candle
            df_last = self.db.get_latest_data(self.symbol, limit=1)
            
            if df_last.empty:
                logger.warning("DB empty. Performing full download...")
                redis_publisher.log("warning", "DB empty. Performing full download...")
                return self.download_historical_data()
            
            # Get last date (NY time)
            last_db_time = df_last['date'].iloc[-1]

            logger.info(f"Last timestamp in dataset: {last_db_time}")
            redis_publisher.log("success", f"Last timestamp in dataset: {last_db_time}")

            # --- STEP 3: Comparison ---
            # If the last candle in DB is equal (or later) to expected, we are good.
            if last_db_time >= expected_candle_time:
                logger.info(f"Data updated. (Last: {last_db_time})")
                return self.db.get_latest_data(self.symbol, limit=300)
            
            # If we are here, data is MISSING.
            # Calculate the "gap" to decide how much to download
            gap = expected_candle_time - last_db_time
            
            logger.info(f"Missing candle {expected_candle_time}. Time gap: {gap}")
            redis_publisher.log("warning", f"⏳ Data gap detected: {gap}")
            redis_publisher.publish("data-gap", {
                "gap_duration": str(gap),
                "missing_from": str(last_db_time),
                "missing_to": str(expected_candle_time)
            })
            
             # --- STEP 4: Smart Download Strategy ---
            if gap < timedelta(minutes=10):
                # Missing only last candle (or slightly more). Fast download.
                duration_str = '1800 S' # 30 min
            elif gap < timedelta(days=2):
                # Day change (e.g. yesterday evening -> this morning)
                duration_str = '2 D'
            else:
                # Weekend or bot off for days
                duration_str = '1 W'
            
            # --- STEP 5: Download from IB ---
            logger.info(f"Requesting data from IB (Duration: {duration_str})...")
            redis_publisher.publish("data-update", {
                "status": "downloading",
                "duration": duration_str,
                "retries": max_retries
            })

            for attempt in range(max_retries):
                bars = self.ib.reqHistoricalData(
                    self.contract,
                    endDateTime='',
                    durationStr=duration_str,
                    barSizeSetting='5 mins',
                    whatToShow='TRADES',
                    useRTH=True,
                    formatDate=1
                )

                if bars:
                    # Convert and filter only new days
                    new_df = util.df(bars)
                    new_df['date'] = pd.to_datetime(new_df['date'], utc=True).dt.tz_convert('America/New_York')
                    
                    # Filter: Save only what is NEW compared to DB
                    new_candles = new_df[new_df['date'] > last_db_time]
        
                    if not new_candles.empty:
                        self.db.save_candles(new_candles, self.symbol)

                        logger.info(f"✅ Added {len(new_candles)} new candles.")
                        redis_publisher.log("success", f"✅ Added {len(new_candles)} new candles")
                        
                        # Send update info
                        redis_publisher.publish("data-update", {
                            "status": "updated",
                            "new_candles": len(new_candles),
                            "latest_time": str(new_candles['date'].max())
                        })
                        
                        # 5. Return last 300 candles from DB to bot (for indicator calculation)
                        return self.db.get_latest_data(self.symbol, limit=300)

                # Retry
                if attempt < max_retries - 1:
                    logger.warning(f"Candle not yet available, retry {attempt+1}/{max_retries} in {retry_delay}s...")
                    redis_publisher.log("warning", f"⏳ Retry {attempt+1}/{max_retries} in {retry_delay}s...")
                    time.sleep(retry_delay)

            # Fallback: if expected candle not found after all retries
            logger.warning(f"⚠️ Candle {expected_candle_time} not found after {max_retries} attempts")
            redis_publisher.log("warning", f"⚠️ Candle not available after {max_retries} attempts")
            redis_publisher.publish("data-update", {
                "status": "failed",
                "expected_candle": str(expected_candle_time),
                "attempts": max_retries
            })
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error updating data: {e}")
            redis_publisher.send_error(f"Error updating data: {str(e)}")
            redis_publisher.publish("data-update", {
                "status": "error",
                "error": str(e),
                "symbol": self.symbol
            })
            return pd.DataFrame()