import pandas as pd
import pandas_ta as ta
import os
from src.logger import logger
from src.database import DatabaseHandler
from src.redis_publisher import redis_publisher
from config import SYMBOL

class IndicatorCalculator:
    """Calculates technical indicators for trading strategy."""
    
    def __init__(self):
        """Initializes indicator calculator."""
        self.db = DatabaseHandler()
        self.symbol = SYMBOL

        # Indicator parameters
        self.params = {
            'ATR_LENGTH': 14,
            'SMA_LENGTH': 200,
            'WILLR_LENGTH': 10
        }
        
        # Minimum number of candles required to calculate all indicators
        self.min_candles_required = max(self.params.values()) + 50  # Buffer extra

        # Path to save data
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        self.data_file = os.path.join(self.data_dir, f'{self.symbol}_5min.csv')

        # Send indicator configuration to dashboard
        redis_publisher.publish("indicators-config", {
            "symbol": self.symbol,
            "parameters": self.params,
            "min_candles_required": self.min_candles_required,
            "indicators": ["ATR_14", "SMA_200", "WILLR_10"]
        })
        redis_publisher.log("info", f"📊 Configured indicators: ATR({self.params['ATR_LENGTH']}), SMA({self.params['SMA_LENGTH']}), WILLR({self.params['WILLR_LENGTH']})")
            
    def calculate_all(self, df, timezone='America/New_York'):
        """
        Calculates all indicators necessary for the strategy.
        
        Args:
            df: DataFrame with OHLCV columns
            timezone: Timezone for date conversion
            
        Returns:
            DataFrame with added indicators
        """
        try:
            # Create a copy to avoid modifying original
            df = df.copy()
            
            # Ensure date is in datetime format with timezone
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert(timezone)
            
            # Verify enough data
            if len(df) < self.min_candles_required:
                logger.warning(f"Insufficient data to calculate all indicators. "
                             f"Required: {self.min_candles_required}, Available: {len(df)}")
                redis_publisher.log("warning", f"⚠️ Insufficient data: {len(df)}/{self.min_candles_required} candles")
                redis_publisher.publish("indicators-warning", {
                    "type": "insufficient_data",
                    "required": self.min_candles_required,
                    "available": len(df)
                })
            
            # Calculate ATR (Average True Range)
            df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=self.params['ATR_LENGTH'])
            
            # Calculate SMA (Simple Moving Average) 200
            df['SMA_200'] = ta.sma(df['close'], length=self.params['SMA_LENGTH'])
            
            # Calculate Williams %R
            df['WILLR_10'] = ta.willr(df['high'], df['low'], df['close'], length=self.params['WILLR_LENGTH'])
            
            df.to_csv(self.data_file, index=False)
            self.db.save_candles(df, self.symbol)
            return df
            
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            redis_publisher.send_error(f"Indicator calculation error: {str(e)}")
            redis_publisher.publish("indicators-calculation", {
                "status": "error",
                "error": str(e)
            })
            return df
        
    def calculate_incremental(self, df):
        """
        Calculates indicators only for the last 5 rows.
        
        Args:
            df: Complete DataFrame
            
        Returns:
            DataFrame with updated indicators
        """
        try:
            return self.calculate_all(df)
            df = df.copy()
            
            # Identify if indicators already exist
            indicator_columns = ['ATR_14', 'SMA_200', 'WILLR_10']
            has_indicators = all(col in df.columns for col in indicator_columns)
            
            if not has_indicators or len(df) < 200:
                # First time or insufficient data, calculate all
                logger.info("Full indicator calculation...")
                return self.calculate_all(df)
            
            # Calculate only for last 5 rows
            start_idx = max(0, len(df) - 250)  # Ensure enough data for indicators
            end_idx = len(df)
            
            # Take a sufficient subset for calculation
            subset = df.iloc[start_idx:end_idx].copy()
            
            # Calculate indicators on subset
            subset['ATR_14'] = ta.atr(subset['high'], subset['low'], subset['close'], length=14)
            subset['SMA_200'] = ta.sma(subset['close'], length=200)
            subset['WILLR_10'] = ta.willr(subset['high'], subset['low'], subset['close'], length=10)
            
            # Update only last 5 rows in original DataFrame
            last_5_start = max(0, len(df) - 5)
            
            df.loc[df.index[last_5_start:], 'ATR_14'] = subset.iloc[-5:]['ATR_14'].values
            df.loc[df.index[last_5_start:], 'SMA_200'] = subset.iloc[-5:]['SMA_200'].values
            df.loc[df.index[last_5_start:], 'WILLR_10'] = subset.iloc[-5:]['WILLR_10'].values
            
            logger.info(f"Updated indicators for last {min(5, len(df))} rows")
            df.to_csv(self.data_file, index=False)
            return df
            
        except Exception as e:
            logger.error(f"Error in incremental calculation: {e}")
            return self.calculate_all(df)