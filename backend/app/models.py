from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class MessageType(str, Enum):
    """Tipi di messaggi supportati"""
    ACCOUNT_UPDATE = "account-update"
    POSITION_UPDATE = "position-update"
    ORDER_UPDATE = "order-update"
    PNL_UPDATE = "pnl-update"
    LOG = "log"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    INITIAL_STATE = "initial-state"

class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class LogMessage(BaseModel):
    level: LogLevel
    message: str
    timestamp: str
    details: Optional[Dict[str, Any]] = None

class AccountInfo(BaseModel):
    net_liquidation: Optional[float] = 0
    buying_power: Optional[float] = 0
    total_cash: Optional[float] = 0
    daily_pnl: Optional[float] = 0
    timestamp: Optional[datetime] = None

class Position(BaseModel):
    symbol: str
    position: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: Optional[float] = 0

class Order(BaseModel):
    order_id: int
    symbol: str
    action: str  # BUY/SELL
    quantity: float
    status: str
    filled: float
    remaining: float
    avg_fill_price: Optional[float] = None
    timestamp: Optional[datetime] = None

class PnLUpdate(BaseModel):
    daily_pnl: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: Optional[float] = None

class WebSocketMessage(BaseModel):
    type: MessageType
    payload: Dict[str, Any]
    timestamp: str