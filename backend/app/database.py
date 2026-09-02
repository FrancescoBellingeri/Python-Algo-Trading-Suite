import os
import pandas as pd
import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Float, Integer
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import TIMESTAMP
from dotenv import load_dotenv

# 1. Load environment variables
load_dotenv()

# 2. Standard logger configuration
logger = logging.getLogger("database")

# 3. SQLAlchemy Base
Base = declarative_base()

# ====================
# ORM MODELS
# ====================

class MarketData(Base):
    """Historical candle data table"""
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
    """Completed trades history table"""
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
# DRAWDOWN
# ====================

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def chronological(trades):
    """Oldest first. Rows with no exit_time sort last, ties broken by insert id."""
    return sorted(trades, key=lambda t: (t.exit_time is None, t.exit_time or _EPOCH, t.id or 0))


def resolve_starting_equity(sorted_trades):
    """Recover the account equity the trade history started from.

    Every row stores pnl_percent = pnl_dollar / account_equity * 100, where
    account_equity is the snapshot the bot takes once when a session starts
    (live/src/execution_handler.py). Inverting that on a trade gives back the
    equity it was measured against; rolling off the PnL booked before it gives
    the equity at the beginning of the history.

    Set STARTING_EQUITY in the environment to skip the derivation.
    """
    override = os.getenv("STARTING_EQUITY")
    if override:
        try:
            value = float(override)
            if value > 0:
                return value
            logger.warning(f"STARTING_EQUITY must be positive, ignoring {override!r}")
        except ValueError:
            logger.warning(f"STARTING_EQUITY is not a number, ignoring {override!r}")

    pnl_before = 0.0
    for t in sorted_trades:
        pnl = t.pnl_dollar or 0.0
        pct = t.pnl_percent or 0.0
        # pct is 0 when the bot had no equity snapshot at the time: not invertible
        if pnl and pct:
            starting = pnl / (pct / 100) - pnl_before
            if starting > 0:
                return starting
        pnl_before += pnl
    return None


def compute_drawdown(sorted_trades, starting_equity):
    """Deepest peak-to-trough slide of the realized equity curve.

    equity(i) = starting_equity + cumulative realized PnL up to trade i.

    Both figures come off that single curve and describe the SAME slide, so the
    dashboard can put them on one line without them contradicting each other.
    The episode reported is the deepest one in percent (the textbook max
    drawdown) and the dollar figure is that same episode's depth.

    Percent needs the starting equity: measured against the cumulative-PnL peak
    instead, an early $600 peak followed by a $9,500 slide reads as a "250%
    drawdown". When the equity cannot be resolved the percent is reported as 0.0
    rather than fabricated.

    Returns (max_dd_dollar, max_dd_percent, peak_equity).
    """
    cumulative = 0.0
    peak_cumulative = 0.0
    max_dd_dollar = 0.0
    max_dd_percent = 0.0
    peak_equity = starting_equity

    for t in sorted_trades:
        cumulative += t.pnl_dollar or 0.0
        peak_cumulative = max(peak_cumulative, cumulative)
        # equity - peak_equity == cumulative - peak_cumulative, so the depth in
        # dollars is the same whether or not the starting equity is known.
        depth = peak_cumulative - cumulative

        if starting_equity:
            episode_peak = starting_equity + peak_cumulative
            percent = (depth / episode_peak * 100) if episode_peak > 0 else 0.0
            if percent > max_dd_percent:
                max_dd_percent, max_dd_dollar, peak_equity = percent, depth, episode_peak
        elif depth > max_dd_dollar:
            max_dd_dollar = depth

    return max_dd_dollar, max_dd_percent, peak_equity


# ====================
# DATABASE HANDLER
# ====================

class DatabaseHandler:
    def __init__(self):
        # Use environment variable or fallback
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
        """Save candles (without calling Redis)"""
        if df.empty: return False
        
        session = self.Session()
        try:
            for _, row in df.iterrows():
                ts = row['date']
                if isinstance(ts, str): ts = pd.to_datetime(ts)
                
                # UTC handling
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
        """Read historical data for bot or charts"""
        query = f"SELECT * FROM market_data WHERE symbol = '{symbol}' ORDER BY timestamp DESC LIMIT {limit}"
        try:
            df = pd.read_sql(query, self.engine)
            if df.empty: return df
            
            # Convert UTC -> NY Time for bot compatibility
            df['date'] = pd.to_datetime(df['timestamp']).dt.tz_convert('America/New_York')
            df = df.rename(columns={'atr_14': 'ATR_14', 'sma_200': 'SMA_200', 'willr_10': 'WILLR_10'})
            return df.sort_values('date').reset_index(drop=True)
        except Exception as e:
            logger.error(f"DB read error: {e}")
            return pd.DataFrame()

    # --- Trade Methods ---

    def save_trade(self, symbol, entry_price, exit_price, quantity, entry_time, exit_time, pnl_dollar, pnl_percent, exit_reason):
        """Save completed trade"""
        session = self.Session()
        try:
            # Ensure UTC
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
        """API: Retrieve trade list"""
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
        """API: Count total trades"""
        session = self.Session()
        try:
            query = session.query(Trade)
            if symbol: query = query.filter(Trade.symbol == symbol)
            return query.count()
        finally:
            session.close()

    def calculate_stats(self, symbol=None):
        """API: Calculate statistics"""
        session = self.Session()
        try:
            query = session.query(Trade)
            if symbol: query = query.filter(Trade.symbol == symbol)
            trades = query.all()
            
            if not trades:
                return {
                    'total_trades': 0, 'win_rate_percent': 0.0, 'total_pnl_dollar': 0.0,
                    'avg_win_dollar': 0.0, 'avg_loss_dollar': 0.0,
                    'max_drawdown_dollar': 0.0, 'max_drawdown_percent': 0.0,
                    'starting_equity': None, 'max_drawdown_peak_equity': None
                }
            
            total_trades = len(trades)
            winners = [t for t in trades if (t.pnl_dollar or 0) > 0]
            losers = [t for t in trades if (t.pnl_dollar or 0) <= 0]
            
            total_pnl = sum(t.pnl_dollar or 0 for t in trades)
            win_rate = (len(winners) / total_trades * 100)
            avg_win = (sum(t.pnl_dollar or 0 for t in winners) / len(winners)) if winners else 0
            avg_loss = (sum(t.pnl_dollar or 0 for t in losers) / len(losers)) if losers else 0
            
            # Drawdown: one equity curve, so the dollar and percent figures
            # describe the same slide. See compute_drawdown() above.
            ordered = chronological(trades)
            starting_equity = resolve_starting_equity(ordered)
            if starting_equity is None:
                logger.warning(
                    "Could not resolve starting equity (no trade has a usable "
                    "pnl_percent); max_drawdown_percent reported as 0. "
                    "Set STARTING_EQUITY to fix."
                )
            max_dd, max_dd_pct, peak_equity = compute_drawdown(ordered, starting_equity)
            
            return {
                'total_trades': total_trades,
                'win_rate_percent': round(win_rate, 2),
                'total_pnl_dollar': round(total_pnl, 2),
                'avg_win_dollar': round(avg_win, 2),
                'avg_loss_dollar': round(avg_loss, 2),
                'max_drawdown_dollar': round(max_dd, 2),
                'max_drawdown_percent': round(max_dd_pct, 2),
                'starting_equity': round(starting_equity, 2) if starting_equity else None,
                'max_drawdown_peak_equity': round(peak_equity, 2) if peak_equity else None
            }
        except Exception as e:
            logger.error(f"Error calculating stats: {e}")
            return None
        finally:
            session.close()