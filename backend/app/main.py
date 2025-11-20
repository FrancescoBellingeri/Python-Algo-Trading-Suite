# backend/app/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import json
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from typing import Optional
import threading

from .websocket_manager import ConnectionManager
from .redis_client import RedisManager
from .models import MessageType, WebSocketMessage

# Carica variabili ambiente
load_dotenv()

# Inizializza FastAPI
app = FastAPI(
    title="Trading Bot WebSocket Server",
    description="Real-time trading data streaming server",
    version="1.0.0"
)

# Configurazione CORS
origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inizializza managers
manager = ConnectionManager()
redis_manager = RedisManager(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=int(os.getenv("REDIS_DB", 0))
)

# Flag per stato del sistema
system_status = {
    "redis_connected": False,
    "bot_connected": False,
    "last_bot_message": None,
    "server_start_time": datetime.now().isoformat()
}

# Store the main event loop
main_loop = None

# ==================== EVENTI STARTUP/SHUTDOWN ====================

@app.on_event("startup")
async def startup_event():
    """Inizializzazione all'avvio del server"""
    global main_loop
    print("🚀 Starting Trading WebSocket Server...")
    
    # Store the main event loop
    main_loop = asyncio.get_running_loop()
    
    # Connetti a Redis
    redis_connected = await redis_manager.connect()
    system_status["redis_connected"] = redis_connected
    
    if redis_connected:
        print("✅ Redis connection established")
        
        # Recupera stato precedente se esiste
        saved_state = await redis_manager.get_state("trading:current_state")
        if saved_state:
            manager.current_state = saved_state
            print("✅ Previous state restored from Redis")
        
        # Sottoscrivi al canale del bot con callback modificato
        def handle_bot_message(message):
            """Gestisce messaggi ricevuti dal bot via Redis"""
            try:
                # Aggiorna stato sistema
                system_status["bot_connected"] = True
                system_status["last_bot_message"] = datetime.now().isoformat()
                
                # Estrai tipo e payload
                msg_type = message.get("type", "unknown")
                payload = message.get("payload", {})
                
                # Aggiorna stato interno
                manager.update_state(msg_type, payload)
                
                # Schedule async operations in the main loop
                if main_loop and main_loop.is_running():
                    # Salva stato in Redis
                    asyncio.run_coroutine_threadsafe(
                        redis_manager.set_state("trading:current_state", manager.current_state),
                        main_loop
                    )
                    
                    # Broadcast ai client WebSocket
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast_json(msg_type, payload),
                        main_loop
                    )
                
                # Log per debug (escludi i log per evitare loop)
                if msg_type != "log":
                    print(f"📨 Received from bot: {msg_type}")
                    
            except Exception as e:
                print(f"❌ Error handling bot message: {e}")
        
        # Avvia sottoscrizione
        redis_manager.subscribe_sync("trading-bot-channel", handle_bot_message)
        print("✅ Subscribed to trading-bot-channel")
    else:
        print("⚠️ Running without Redis connection")

@app.on_event("shutdown")
async def shutdown_event():
    """Pulizia alla chiusura del server"""
    print("🛑 Shutting down server...")
    
    # Salva stato finale
    if redis_manager.async_client:
        await redis_manager.set_state("trading:current_state", manager.current_state)
    
    # Disconnetti Redis
    await redis_manager.disconnect()
    
    # Chiudi tutte le connessioni WebSocket
    for ws in list(manager.active_connections):
        await ws.close()
    
    print("✅ Server shutdown complete")

# ==================== WEBSOCKET ENDPOINT ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint per la dashboard Vue
    Gestisce comunicazione real-time con i client
    """
    await manager.connect(websocket)
    
    try:
        while True:
            # Ricevi messaggi dal client
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                msg_type = message.get("type")
                
                # Gestisci richieste specifiche del client
                if msg_type == "ping":
                    # Heartbeat
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif msg_type == "request-state":
                    # Richiesta stato completo
                    await manager.send_initial_state(websocket)
                
                elif msg_type == "request-positions":
                    # Richiesta solo posizioni
                    await websocket.send_json({
                        "type": "positions-update",
                        "payload": manager.current_state["positions"],
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif msg_type == "request-orders":
                    # Richiesta solo ordini
                    await websocket.send_json({
                        "type": "orders-update",
                        "payload": manager.current_state["orders"],
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif msg_type == "clear-logs":
                    # Clear logs (solo per questo client)
                    manager.current_state["logs"] = []
                    await manager.broadcast_json("logs-cleared", {})
                
                elif msg_type == "command":
                    # Invia comando al bot via Redis
                    command_payload = message.get("payload", {})
                    
                    # Usa il main loop per operazioni async
                    if main_loop and main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            redis_manager.publish("trading-bot-commands", command_payload),
                            main_loop
                        )
                    
                    await websocket.send_json({
                        "type": "command-sent",
                        "payload": {"status": "success", "command": command_payload},
                        "timestamp": datetime.now().isoformat()
                    })
                    
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "payload": {"message": "Invalid JSON"},
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                print(f"Error processing client message: {e}")
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        await manager.disconnect(websocket)

# ==================== REST API ENDPOINTS ====================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "server_time": datetime.now().isoformat(),
        "system_status": system_status,
        "websocket_stats": manager.get_stats()
    }

@app.get("/api/status")
async def get_status():
    """Stato dettagliato del sistema"""
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
        },
        "state_summary": {
            "positions_count": len(manager.current_state["positions"]),
            "active_orders": len([o for o in manager.current_state["orders"] if o.get("status") != "Filled"]),
            "logs_count": len(manager.current_state["logs"]),
            "last_update": manager.current_state["last_update"]
        }
    }

@app.get("/api/state")
async def get_current_state():
    """Recupera stato corrente completo"""
    return JSONResponse(content=manager.current_state)

@app.get("/api/positions")
async def get_positions():
    """Recupera posizioni correnti"""
    return JSONResponse(content=manager.current_state["positions"])

@app.get("/api/orders")
async def get_orders(status: Optional[str] = None):
    """Recupera ordini, opzionalmente filtrati per status"""
    orders = manager.current_state["orders"]
    
    if status:
        orders = [o for o in orders if o.get("status") == status]
    
    return JSONResponse(content=orders)

@app.get("/api/pnl")
async def get_pnl():
    """Recupera P&L corrente"""
    return JSONResponse(content=manager.current_state["pnl"])

@app.get("/api/logs")
async def get_logs(limit: int = 100, level: Optional[str] = None):
    """Recupera logs, opzionalmente filtrati"""
    logs = manager.current_state["logs"]
    
    if level:
        logs = [l for l in logs if l.get("level") == level]
    
    # Limita numero di logs
    if len(logs) > limit:
        logs = logs[-limit:]
    
    return JSONResponse(content=logs)

@app.post("/api/command")
async def send_command(command: dict):
    """Invia comando al bot via Redis"""
    if not redis_manager.async_client:
        raise HTTPException(status_code=503, detail="Redis not connected")
    
    success = await redis_manager.publish("trading-bot-commands", command)
    
    if success:
        return {"status": "success", "command": command, "timestamp": datetime.now().isoformat()}
    else:
        raise HTTPException(status_code=500, detail="Failed to send command")

# ==================== ERROR HANDLERS ====================

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Gestione errori generali"""
    print(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "timestamp": datetime.now().isoformat()
        }
    )