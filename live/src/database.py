import pandas as pd
from sqlalchemy import create_engine, Column, String, Float, DateTime, Integer
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import TIMESTAMP
from config import DATABASE_URL
from src.logger import logger
from src.redis_publisher import redis_publisher

Base = declarative_base()

class MarketData(Base):
    """
    PostgreSQL table model.
    """
    __tablename__ = 'market_data'

    # Composite primary key: Symbol + Timestamp
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)
    symbol = Column(String(10), primary_key=True)
    
    # OHLCV Data
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    
    # Indicators
    atr_14 = Column(Float, nullable=True)
    sma_200 = Column(Float, nullable=True)
    willr_10 = Column(Float, nullable=True)

class DatabaseHandler:
    def __init__(self):
        # Create Postgres connection engine
        self.engine = create_engine(DATABASE_URL, echo=False)
        
        # Automatically create tables if they don't exist
        try:
            Base.metadata.create_all(self.engine)
            logger.info("PostgreSQL DB connection established and tables verified.")
            redis_publisher.log("success", "PostgreSQL DB connection established and tables verified.")
        except Exception as e:
            logger.error(f"DB connection error: {e}")
            redis_publisher.send_error(f"DB connection error: {e}")
            raise e
        
        self.Session = sessionmaker(bind=self.engine)

    def save_candles(self, df, symbol):
        """Save data (Upsert/Merge)."""
        if df.empty: return
        
        session = self.Session()
        try:
            for _, row in df.iterrows():
                # Robust Timezone handling
                ts = row['date']
                if isinstance(ts, str):
                    ts = pd.to_datetime(ts)
                
                # Postgres requires explicit UTC
                if ts.tzinfo is None:
                    ts = ts.tz_localize('UTC')
                else:
                    ts = ts.tz_convert('UTC')

                candle = MarketData(
                    timestamp=ts,
                    symbol=symbol,
                    open=row['open'], high=row['high'], low=row['low'], 
                    close=row['close'], volume=row.get('volume', 0),
                    atr_14=row.get('ATR_14'),
                    sma_200=row.get('SMA_200'),
                    willr_10=row.get('WILLR_10')
                )
                
                session.merge(candle)
            
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"DB save error: {e}")
            redis_publisher.send_error(f"DB save error: {e}")
            return False
        finally:
            session.close()

    def get_latest_data(self, symbol, limit=1000):
        """Reads data from DB."""
        query = f"""
        SELECT * FROM (
            SELECT * FROM market_data 
            WHERE symbol = '{symbol}' 
            ORDER BY timestamp DESC 
            LIMIT {limit}
        ) AS sub
        ORDER BY timestamp ASC
        """
        try:
            df = pd.read_sql(query, self.engine)
            
            if df.empty:
                return df
            
            # Convert timestamp column (arriving as UTC) to NY Time for the bot
            df['date'] = pd.to_datetime(df['timestamp']).dt.tz_convert('America/New_York')
            
            # Rename columns for compatibility with the rest of the code
            df = df.rename(columns={
                'atr_14': 'ATR_14',
                'sma_200': 'SMA_200',
                'willr_10': 'WILLR_10'
            })
                
            return df.sort_values('date').reset_index(drop=True)
        except Exception as e:
            logger.error(f"DB read error: {e}")
            redis_publisher.send_error(f"DB read error: {e}")
            return pd.DataFrame()