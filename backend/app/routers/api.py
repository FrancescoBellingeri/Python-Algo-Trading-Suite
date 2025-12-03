from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from ..services import manager, redis_manager, db_handler, system_status

router = APIRouter(prefix="/api")

# --- HEALTH CHECK & STATUS ---
@router.get("/status")
async def get_status():
    """Stato dettagliato del sistema per il footer della dashboard"""
    return {
        "server": {
            "status": "online",
            "uptime": system_status["server_start_time"],
            "current_time": datetime.now().isoformat()
        },
        "connections": {
            "websocket": manager.get_stats(),
            "redis": system_status["redis_connected"],
            "bot": system_status["bot_connected"],
            "last_bot_message": system_status["last_bot_message"]
        }
    }

# --- LIVE STATE (DEBUG) ---
@router.get("/state")
async def get_current_state():
    """Snapshot dello stato in memoria (utile per debug se il WS non va)"""
    return manager.current_state

# --- DATABASE: HISTORY & STATS (NUOVI) ---
@router.get("/history")
async def get_trade_history(
    limit: int = 50, 
    offset: int = 0, 
    symbol: Optional[str] = None
):
    """Storico trade dal DB PostgreSQL"""
    try:
        trades = db_handler.get_trades(limit=limit, offset=offset, symbol=symbol)
        total = db_handler.get_total_trade_count(symbol=symbol)
        return {"data": trades, "total": total, "page_size": limit, "offset": offset}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB Error: {str(e)}")

@router.get("/stats")
async def get_trade_stats(symbol: Optional[str] = None):
    """Statistiche aggregate dal DB"""
    try:
        return db_handler.calculate_stats(symbol=symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB Error: {str(e)}")

# --- COMMANDS ---
@router.post("/command")
async def send_command(command: dict):
    """Invia comandi manuali al bot via Redis"""
    if not redis_manager.async_client:
        raise HTTPException(status_code=503, detail="Redis not connected")
    
    success = await redis_manager.publish("trading-bot-commands", command)
    if success:
        return {"status": "success", "command": command}
    else:
        raise HTTPException(status_code=500, detail="Failed to publish command")