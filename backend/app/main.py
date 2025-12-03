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

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI(
    title="Trading Bot WebSocket Server",
    description="Real-time trading data streaming server",
    version="1.0.0"
)

# CORS Configuration
origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize managers
manager = ConnectionManager()
redis_manager = RedisManager(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=int(os.getenv("REDIS_DB", 0))
)

# System status flags
system_status = {
    "redis_connected": False,
    "bot_connected": False,
    "last_bot_message": None,
    "server_start_time": datetime.now().isoformat()
}

# Store the main event loop
main_loop = None

# ==================== STARTUP/SHUTDOWN EVENTS ====================

@app.on_event("startup")
async def startup_event():
    """Server startup initialization"""
    global main_loop
    print("🚀 Starting Trading WebSocket Server...")
    
    # Store the main event loop
    main_loop = asyncio.get_running_loop()
    
    # Connect to Redis
    redis_connected = await redis_manager.connect()
    system_status["redis_connected"] = redis_connected
    
    if redis_connected:
        print("✅ Redis connection established")
        
        # Retrieve previous state if exists
        saved_state = await redis_manager.get_state("trading:current_state")
        if saved_state:
            manager.current_state = saved_state
            print("✅ Previous state restored from Redis")
        
        # Subscribe to bot channel with modified callback
        def handle_bot_message(message):
            """Handles messages received from bot via Redis"""
            try:
                # Update system status
                system_status["bot_connected"] = True
                system_status["last_bot_message"] = datetime.now().isoformat()
                
                # Extract type and payload
                msg_type = message.get("type", "unknown")
                payload = message.get("payload", {})
                
                # Update internal state
                manager.update_state(msg_type, payload)
                
                # Schedule async operations in the main loop
                if main_loop and main_loop.is_running():
                    # Save state to Redis
                    asyncio.run_coroutine_threadsafe(
                        redis_manager.set_state("trading:current_state", manager.current_state),
                        main_loop
                    )
                    
                    # Broadcast to WebSocket clients
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast_json(msg_type, payload),
                        main_loop
                    )
                
                # Debug log (exclude logs to avoid loops)
                if msg_type != "log":
                    print(f"📨 Received from bot: {msg_type}")
                    
            except Exception as e:
                print(f"❌ Error handling bot message: {e}")
        
        # Start subscription
        redis_manager.subscribe_sync("trading-bot-channel", handle_bot_message)
        print("✅ Subscribed to trading-bot-channel")
    else:
        print("⚠️ Running without Redis connection")

@app.on_event("shutdown")
async def shutdown_event():
    """Server shutdown cleanup"""
    print("🛑 Shutting down server...")
    
    # Save final state
    if redis_manager.async_client:
        await redis_manager.set_state("trading:current_state", manager.current_state)
    
    # Disconnect Redis
    await redis_manager.disconnect()
    
    # Close all WebSocket connections
    for ws in list(manager.active_connections):
        await ws.close()
    
    print("✅ Server shutdown complete")

# ==================== WEBSOCKET ENDPOINT ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for Vue dashboard
    Handles real-time communication with clients
    """
    await manager.connect(websocket)
    
    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                msg_type = message.get("type")
                
                # Handle specific client requests
                if msg_type == "ping":
                    # Heartbeat
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif msg_type == "request-state":
                    # Full state request
                    await manager.send_initial_state(websocket)
                
                elif msg_type == "request-positions":
                    # Positions only request
                    await websocket.send_json({
                        "type": "positions-update",
                        "payload": manager.current_state["positions"],
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif msg_type == "request-orders":
                    # Orders only request
                    await websocket.send_json({
                        "type": "orders-update",
                        "payload": manager.current_state["orders"],
                        "timestamp": datetime.now().isoformat()
                    })
                
                elif msg_type == "clear-logs":
                    # Clear logs (only for this client)
                    manager.current_state["logs"] = []
                    await manager.broadcast_json("logs-cleared", {})
                
                elif msg_type == "command":
                    # Send command to bot via Redis
                    command_payload = message.get("payload", {})
                    
                    # Use main loop for async operations
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
    """Detailed system status"""
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
    """Retrieve full current state"""
    return JSONResponse(content=manager.current_state)

@app.get("/api/positions")
async def get_positions():
    """Retrieve current positions"""
    return JSONResponse(content=manager.current_state["positions"])

@app.get("/api/orders")
async def get_orders(status: Optional[str] = None):
    """Retrieve orders, optionally filtered by status"""
    orders = manager.current_state["orders"]
    
    if status:
        orders = [o for o in orders if o.get("status") == status]
    
    return JSONResponse(content=orders)

@app.get("/api/pnl")
async def get_pnl():
    """Retrieve current P&L"""
    return JSONResponse(content=manager.current_state["pnl"])

@app.get("/api/logs")
async def get_logs(limit: int = 100, level: Optional[str] = None):
    """Retrieve logs, optionally filtered"""
    logs = manager.current_state["logs"]
    
    if level:
        logs = [l for l in logs if l.get("level") == level]
    
    # Limit number of logs
    if len(logs) > limit:
        logs = logs[-limit:]
    
    return JSONResponse(content=logs)

@app.post("/api/command")
async def send_command(command: dict):
    """Send command to bot via Redis"""
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
    """General error handling"""
    print(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "timestamp": datetime.now().isoformat()
        }
    )