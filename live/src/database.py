import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Float, Integer
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

class Trade(Base):
    """
    PostgreSQL table for completed trades.
    """
    __tablename__ = 'trades'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=True)
    entry_time = Column(TIMESTAMP(timezone=True), nullable=True)
    exit_time = Column(TIMESTAMP(timezone=True), nullable=True)
    pnl_dollar = Column(Float, nullable=True)
    pnl_percent = Column(Float, nullable=True)
    exit_reason = Column(String(20), nullable=True)  # "TRAILING_STOP" or "SMA_CROSS"

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
    
    # ==================== Trade Management Methods ====================
    
    def save_trade(self, symbol: str, entry_price: float, exit_price: float, quantity: int,
                   entry_time, exit_time, pnl_dollar: float, pnl_percent: float, exit_reason: str):
        """
        Save a completed trade to the database.
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            exit_price: Exit price
            quantity: Number of shares
            entry_time: Entry timestamp (datetime object)
            exit_time: Exit timestamp (datetime object)
            pnl_dollar: Profit/Loss in dollars
            pnl_percent: Profit/Loss in percentage
            exit_reason: "TRAILING_STOP" or "SMA_CROSS"
            
        Returns:
            int: ID of saved trade, or None on error
        """
        session = self.Session()
        try:
            if not entry_time:
                entry_time = datetime.now()
            if not exit_time:
                exit_time = datetime.now()
            
            # Ensure timestamps are timezone-aware (UTC)
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
            
            logger.info(f"Trade saved successfully: ID {trade.id}")
            redis_publisher.log("success", f"✅ Trade saved to database: ID {trade.id}")
            return trade.id
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving trade: {e}")
            redis_publisher.send_error(f"Error saving trade: {e}")
            return None
        finally:
            session.close()
    
    def get_trades(self, limit: int = 50, offset: int = 0, symbol: str = None):
        """
        Retrieve trades with pagination.
        
        Args:
            limit: Maximum number of trades to return
            offset: Number of trades to skip
            symbol: Optional symbol filter
            
        Returns:
            List of dictionaries with trade data
        """
        session = self.Session()
        try:
            query = session.query(Trade)
            
            if symbol:
                query = query.filter(Trade.symbol == symbol)
            
            # Order by most recent first
            query = query.order_by(Trade.exit_time.desc())
            
            # Apply pagination
            trades = query.limit(limit).offset(offset).all()
            
            # Convert to dictionaries
            result = []
            for t in trades:
                result.append({
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
                })
            
            return result
        except Exception as e:
            logger.error(f"Error retrieving trades: {e}")
            return []
        finally:
            session.close()
    
    def calculate_stats(self, symbol: str = None):
        """
        Calculate trading performance statistics.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            Dictionary with statistics
        """
        session = self.Session()
        try:
            query = session.query(Trade)
            
            if symbol:
                query = query.filter(Trade.symbol == symbol)
            
            trades = query.all()
            
            if not trades:
                return {
                    'total_trades': 0,
                    'win_rate_percent': 0.0,
                    'total_pnl_dollar': 0.0,
                    'avg_win_dollar': 0.0,
                    'avg_loss_dollar': 0.0,
                    'max_drawdown_dollar': 0.0,
                    'max_drawdown_percent': 0.0
                }
            
            # Calculate statistics
            total_trades = len(trades)
            winning_trades = [t for t in trades if t.pnl_dollar > 0]
            losing_trades = [t for t in trades if t.pnl_dollar <= 0]
            
            win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
            total_pnl = sum(t.pnl_dollar for t in trades)
            
            avg_win = (sum(t.pnl_dollar for t in winning_trades) / len(winning_trades)) if winning_trades else 0
            avg_loss = (sum(t.pnl_dollar for t in losing_trades) / len(losing_trades)) if losing_trades else 0
            
            # Calculate max drawdown
            cumulative_pnl = 0
            peak = 0
            max_dd = 0
            max_dd_pct = 0
            
            # Sort by exit time
            sorted_trades = sorted(trades, key=lambda t: t.exit_time)
            
            for trade in sorted_trades:
                cumulative_pnl += trade.pnl_dollar
                
                if cumulative_pnl > peak:
                    peak = cumulative_pnl
                
                drawdown = peak - cumulative_pnl
                if drawdown > max_dd:
                    max_dd = drawdown
                    max_dd_pct = (drawdown / peak * 100) if peak > 0 else 0
            
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
    
    def get_total_trade_count(self, symbol: str = None):
        """Get total count of trades for pagination."""
        session = self.Session()
        try:
            query = session.query(Trade)
            
            if symbol:
                query = query.filter(Trade.symbol == symbol)
            
            return query.count()
        except Exception as e:
            logger.error(f"Error counting trades: {e}")
            return 0
        finally:
            session.close()