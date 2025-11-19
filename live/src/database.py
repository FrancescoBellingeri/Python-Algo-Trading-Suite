import pandas as pd
from sqlalchemy import create_engine, Column, String, Float, DateTime, Integer
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import TIMESTAMP
from config import DATABASE_URL
from src.logger import logger

Base = declarative_base()

class MarketData(Base):
    """
    Modello della tabella su PostgreSQL.
    """
    __tablename__ = 'market_data'

    # Chiave primaria composta: Simbolo + Timestamp
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)
    symbol = Column(String(10), primary_key=True)
    
    # Dati OHLCV
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    
    # Indicatori
    atr_14 = Column(Float, nullable=True)
    sma_200 = Column(Float, nullable=True)
    willr_10 = Column(Float, nullable=True)

class DatabaseHandler:
    def __init__(self):
        # Creiamo il motore di connessione a Postgres
        self.engine = create_engine(DATABASE_URL, echo=False)
        
        # Crea le tabelle automaticamente se non esistono
        try:
            Base.metadata.create_all(self.engine)
            logger.info("Connessione DB PostgreSQL stabilita e tabelle verificate.")
        except Exception as e:
            logger.error(f"Errore connessione DB: {e}")
            raise e
        
        self.Session = sessionmaker(bind=self.engine)

    def save_candles(self, df, symbol):
        """Salva i dati (Upsert/Merge)."""
        if df.empty: return
        
        session = self.Session()
        try:
            for _, row in df.iterrows():
                # Gestione Timezone robusta
                ts = row['date']
                if isinstance(ts, str):
                    ts = pd.to_datetime(ts)
                
                # Postgres vuole UTC esplicito
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
            logger.error(f"Errore salvataggio DB: {e}")
            return False
        finally:
            session.close()

    def get_latest_data(self, symbol, limit=1000):
        """Legge i dati dal DB."""
        query = f"""
        SELECT * FROM market_data 
        WHERE symbol = '{symbol}' 
        ORDER BY timestamp ASC 
        """
        try:
            df = pd.read_sql(query, self.engine)
            
            if df.empty:
                return df
            
            # Converti colonna timestamp (che arriva come UTC) in NY Time per il bot
            df['date'] = pd.to_datetime(df['timestamp']).dt.tz_convert('America/New_York')
            
            # Rinomina colonne per compatibilità col resto del codice
            df = df.rename(columns={
                'atr_14': 'ATR_14',
                'sma_200': 'SMA_200',
                'willr_10': 'WILLR_10'
            })
            
            # Manteniamo solo le ultime 'limit' righe
            if limit and len(df) > limit:
                df = df.iloc[-limit:]
                
            return df.sort_values('date').reset_index(drop=True)
        except Exception as e:
            logger.error(f"Errore lettura DB: {e}")
            return pd.DataFrame()