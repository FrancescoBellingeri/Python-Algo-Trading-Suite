import os
import pandas as pd
import logging
from sqlalchemy import create_engine, Column, String, Float, Integer
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import TIMESTAMP
from dotenv import load_dotenv

# 1. Caricamento variabili d'ambiente
load_dotenv()

# 2. Configurazione Logger standard
logger = logging.getLogger("database")

# 3. Base SQLAlchemy
Base = declarative_base()

# ====================
# MODELLI ORM
# ====================

class MarketData(Base):
    """Tabella dati storici candele"""
    __tablename__ = 'market_data'

    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)
    symbol = Column(String(10), primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    atr_14 = Column(Float, nullable=True)
    sma_200 = Column(Float, nullable=True)
    willr_10 = Column(Float, nullable=True)

class Trade(Base):
    """Tabella storico trade conclusi"""
    __tablename__ = 'trades'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    entry_time = Column(TIMESTAMP(timezone=True), nullable=False)
    exit_time = Column(TIMESTAMP(timezone=True), nullable=False)
    pnl_dollar = Column(Float, nullable=False)
    pnl_percent = Column(Float, nullable=False)
    exit_reason = Column(String(20), nullable=False)

# ====================
# DATABASE HANDLER
# ====================

class DatabaseHandler:
    def __init__(self):
        # Usa variabile d'ambiente o fallback
        self.db_url = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/trading_bot")
        
        try:
            self.engine = create_engine(self.db_url, echo=False)
            Base.metadata.create_all(self.engine)
            self.Session = sessionmaker(bind=self.engine)
            logger.info("✅ DB connection established")
        except Exception as e:
            logger.error(f"❌ DB connection error: {e}")
            raise e

    # --- Market Data Methods ---

    def save_candles(self, df, symbol):
        """Salva candele (senza chiamare Redis)"""
        if df.empty: return False
        
        session = self.Session()
        try:
            for _, row in df.iterrows():
                ts = row['date']
                if isinstance(ts, str): ts = pd.to_datetime(ts)
                
                # Gestione UTC
                if ts.tzinfo is None: ts = ts.tz_localize('UTC')
                else: ts = ts.tz_convert('UTC')

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
            return False
        finally:
            session.close()

    def get_latest_data(self, symbol, limit=1000):
        """Legge dati storici per il bot o grafici"""
        query = f"SELECT * FROM market_data WHERE symbol = '{symbol}' ORDER BY timestamp DESC LIMIT {limit}"
        try:
            df = pd.read_sql(query, self.engine)
            if df.empty: return df
            
            # Converti UTC -> NY Time per compatibilità bot
            df['date'] = pd.to_datetime(df['timestamp']).dt.tz_convert('America/New_York')
            df = df.rename(columns={'atr_14': 'ATR_14', 'sma_200': 'SMA_200', 'willr_10': 'WILLR_10'})
            return df.sort_values('date').reset_index(drop=True)
        except Exception as e:
            logger.error(f"DB read error: {e}")
            return pd.DataFrame()

    # --- Trade Methods ---

    def save_trade(self, symbol, entry_price, exit_price, quantity, entry_time, exit_time, pnl_dollar, pnl_percent, exit_reason):
        """Salva trade concluso"""
        session = self.Session()
        try:
            # Assicura UTC
            if hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is None:
                entry_time = pd.Timestamp(entry_time, tz='UTC')
            if hasattr(exit_time, 'tzinfo') and exit_time.tzinfo is None:
                exit_time = pd.Timestamp(exit_time, tz='UTC')
            
            trade = Trade(
                symbol=symbol,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                entry_time=entry_time,
                exit_time=exit_time,
                pnl_dollar=pnl_dollar,
                pnl_percent=pnl_percent,
                exit_reason=exit_reason
            )
            session.add(trade)
            session.commit()
            session.refresh(trade)
            return trade.id
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving trade: {e}")
            return None
        finally:
            session.close()

    def get_trades(self, limit=50, offset=0, symbol=None):
        """API: Recupera lista trade"""
        session = self.Session()
        try:
            query = session.query(Trade)
            if symbol: query = query.filter(Trade.symbol == symbol)
            
            trades = query.order_by(Trade.exit_time.desc()).limit(limit).offset(offset).all()
            
            return [{
                'id': t.id,
                'symbol': t.symbol,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'quantity': t.quantity,
                'entry_time': t.entry_time.isoformat(),
                'exit_time': t.exit_time.isoformat(),
                'pnl_dollar': t.pnl_dollar,
                'pnl_percent': t.pnl_percent,
                'exit_reason': t.exit_reason
            } for t in trades]
        finally:
            session.close()

    def get_total_trade_count(self, symbol=None):
        """API: Conta totale trade"""
        session = self.Session()
        try:
            query = session.query(Trade)
            if symbol: query = query.filter(Trade.symbol == symbol)
            return query.count()
        finally:
            session.close()

    def calculate_stats(self, symbol=None):
        """API: Calcola statistiche"""
        session = self.Session()
        try:
            query = session.query(Trade)
            if symbol: query = query.filter(Trade.symbol == symbol)
            trades = query.all()
            
            if not trades:
                return {
                    'total_trades': 0, 'win_rate_percent': 0.0, 'total_pnl_dollar': 0.0,
                    'avg_win_dollar': 0.0, 'avg_loss_dollar': 0.0,
                    'max_drawdown_dollar': 0.0, 'max_drawdown_percent': 0.0
                }
            
            total_trades = len(trades)
            winners = [t for t in trades if t.pnl_dollar > 0]
            losers = [t for t in trades if t.pnl_dollar <= 0]
            
            total_pnl = sum(t.pnl_dollar for t in trades)
            win_rate = (len(winners) / total_trades * 100)
            avg_win = (sum(t.pnl_dollar for t in winners) / len(winners)) if winners else 0
            avg_loss = (sum(t.pnl_dollar for t in losers) / len(losers)) if losers else 0
            
            # Calcolo Drawdown
            cumulative = 0
            peak = 0
            max_dd = 0
            max_dd_pct = 0
            
            for t in sorted(trades, key=lambda x: x.exit_time):
                cumulative += t.pnl_dollar
                peak = max(peak, cumulative)
                dd = peak - cumulative
                max_dd = max(max_dd, dd)
                if peak > 0: max_dd_pct = max(max_dd_pct, (dd/peak)*100)
            
            return {
                'total_trades': total_trades,
                'win_rate_percent': round(win_rate, 2),
                'total_pnl_dollar': round(total_pnl, 2),
                'avg_win_dollar': round(avg_win, 2),
                'avg_loss_dollar': round(avg_loss, 2),
                'max_drawdown_dollar': round(max_dd, 2),
                'max_drawdown_percent': round(max_dd_pct, 2)
            }
        except Exception as e:
            logger.error(f"Error calculating stats: {e}")
            return None
        finally:
            session.close()