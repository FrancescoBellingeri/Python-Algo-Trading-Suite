from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

# ====================
# Enums
# ====================

class MessageType(str, Enum):
    """Supported message types for WebSocket/Redis"""
    PRICE_UPDATE = "price_update"       # Alta frequenza (Ticker)
    POSITION_UPDATE = "position_update" # Cambio stato posizione
    ACCOUNT_UPDATE = "account_update"   # Cambio liquidità
    LOG = "log"                         # Messaggi operativi
    INITIAL_STATE = "initial-state"     # Snapshot all'avvio
    COMMAND = "command"                 # Dal frontend al bot

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
    net_liquidation: float          # Valore totale (Cash + Posizioni)
    daily_pnl: Optional[float] = 0.0
    # Rimosso buying_power e total_cash come richiesto

class Position(BaseModel):
    """Active position details with Strategy Indicators"""
    symbol: str
    shares: int                     # Quantità
    entry_price: float
    current_price: float            # Prezzo attuale per calcolo PnL UI
    unrealized_pnl: float
    
    # Campi specifici della tua strategia (Cruciali per la Dashboard)
    current_trailing_stop: Optional[float] = None
    current_sma_value: Optional[float] = None
    
    # Campo calcolato opzionale (es. quanto manca allo stop in %)
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
    payload: Dict[str, Any] # Può contenere uno dei modelli sopra (convertito in dict)
    timestamp: str