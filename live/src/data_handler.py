import pandas as pd
import os
from ib_insync import Stock, util
from config import SYMBOL, EXCHANGE, CURRENCY
from src.database import DatabaseHandler
from src.connector import Connector
from src.redis_publisher import redis_publisher
import time
from datetime import datetime, timedelta
import pytz

class DataHandler:
    """Handles market data download and update."""
    
    def __init__(self, connector, db_handler):
        """
        Initializes DataHandler.
        
        Args:
            connector: Already connected Connector instance
            db_handler: Shared DatabaseHandler instance
        """
        self.connector = connector
        self.ib = self.connector.ib
        self.db = db_handler
        self.symbol = SYMBOL
        self.contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
        
        # Path to save data
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        self.data_file = os.path.join(self.data_dir, f'{SYMBOL}_5min.csv')
        
    def download_historical_data(self):
        """
        Downloads last 5 Days of historical data to calculate all indicators.
        """
        try:            
            redis_publisher.log("success", f"Downloading 5 Days of historical data for {self.symbol}...")

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
                    redis_publisher.log("success", f"✅ Downloaded and saved {len(df)} candles to Database.")
                    
                return df
            
            redis_publisher.log("success", f"No data downloaded")
            return pd.DataFrame
        except Exception as e:
            redis_publisher.log("error", f"Error downloading historical data: {str(e)}")
            return pd.DataFrame()
    
    def update_data(self, max_retries=10, retry_delay=0.2):
        """
        Updates data with the last 5-minute candle.
        To be executed every day every 5 minutes.
        """
        try:
            # Load existing data
            if not os.path.exists(self.data_file):
                redis_publisher.log("warning", f"Data file not found: {self.data_file}")
                redis_publisher.log("warning", "Run download_historical_data() first")
                return False
            
            df = pd.read_csv(self.data_file)
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
                redis_publisher.log("warning", "DB empty. Performing full download...")
                return self.download_historical_data()
            
            # Get last date (NY time)
            last_db_time = df_last['date'].iloc[-1]

            redis_publisher.log("success", f"Last timestamp in dataset: {last_db_time}")

            # --- STEP 3: Comparison ---
            # If the last candle in DB is equal (or later) to expected, we are good.
            if last_db_time >= expected_candle_time:
                redis_publisher.log("success", f"Data updated. (Last: {last_db_time})")
                return self.db.get_latest_data(self.symbol, limit=300)
            
            # If we are here, data is MISSING.
            # Calculate the "gap" to decide how much to download
            gap = expected_candle_time - last_db_time
            
            redis_publisher.log("warning", f"Missing candle {expected_candle_time}. Time gap: {gap}")
            
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
            redis_publisher.log("info", f"Requesting data from IB (Duration: {duration_str})...")

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

                        redis_publisher.log("success", f"✅ Added {len(new_candles)} new candles.")
                        
                        # 5. Return last 300 candles from DB to bot (for indicator calculation)
                        return self.db.get_latest_data(self.symbol, limit=300)

                # Retry
                if attempt < max_retries - 1:
                    redis_publisher.log("warning", f"Candle not yet available, retry {attempt+1}/{max_retries} in {retry_delay}s...")
                    time.sleep(retry_delay)

            # Fallback: if expected candle not found after all retries
            redis_publisher.log("warning", f"⚠️ Candle {expected_candle_time} not found after {max_retries} attempts")
            redis_publisher.log("warning", f"⚠️ Candle not available after {max_retries} attempts")
            return pd.DataFrame()
        except Exception as e:
            redis_publisher.log("error", f"Error updating data: {str(e)}")
            return pd.DataFrame()