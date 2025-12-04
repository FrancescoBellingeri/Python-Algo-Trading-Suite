from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

# ====================
# Enums
# ====================

class MessageType(str, Enum):
    """Supported message types for WebSocket/Redis"""
    PRICE_UPDATE = "price_update"       # High frequency (Ticker)
    POSITION_UPDATE = "position_update" # Position state change
    ACCOUNT_UPDATE = "account_update"   # Liquidity change
    LOG = "log"                         # Operational messages
    INITIAL_STATE = "initial-state"     # Snapshot at startup
    COMMAND = "command"                 # From frontend to bot

class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class ExitReason(str, Enum):
    """Reasons for exiting a trade"""
    TRAILING_STOP = "TRAILING_STOP"
    SMA_CROSS = "SMA_CROSS"

# ====================
# Real-Time Payload Models (WebSocket)
# ====================

class TickerUpdate(BaseModel):
    """Lightweight price update"""
    symbol: str
    price: float
    change_percent: float = 0.0

class AccountInfo(BaseModel):
    """Essential account info"""
    net_liquidation: float          # Total value (Cash + Positions)
    daily_pnl: Optional[float] = 0.0
    # Removed buying_power and total_cash as requested

class Position(BaseModel):
    """Active position details with Strategy Indicators"""
    symbol: str
    shares: int                     # Quantity
    entry_price: float
    current_price: float            # Current price for UI PnL calculation
    unrealized_pnl: float
    
    # Strategy-specific fields (Crucial for Dashboard)
    current_trailing_stop: Optional[float] = None
    current_sma_value: Optional[float] = None
    
    # Optional calculated field (e.g. how far to stop in %)
    distance_to_stop_pct: Optional[float] = None

class LogMessage(BaseModel):
    level: LogLevel
    message: str
    timestamp: str
    details: Optional[Dict[str, Any]] = None

# ====================
# Database Models (History & API)
# ====================

class Trade(BaseModel):
    """Completed trade record (Matches DB)"""
    id: Optional[int] = None
    symbol: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: datetime
    exit_time: datetime
    pnl_dollar: float
    pnl_percent: float
    exit_reason: ExitReason

class TradeStats(BaseModel):
    """Aggregated Performance Metrics"""
    total_trades: int
    win_rate_percent: float
    total_pnl_dollar: float
    avg_win_dollar: float
    avg_loss_dollar: float
    max_drawdown_dollar: float
    max_drawdown_percent: float

# ====================
# WebSocket Wrapper
# ====================

class WebSocketMessage(BaseModel):
    type: MessageType
    payload: Dict[str, Any] # Can contain one of the models above (converted to dict)
    timestamp: str