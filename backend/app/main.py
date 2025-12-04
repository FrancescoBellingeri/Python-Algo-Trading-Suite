from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Import routers and services
from .routers import api, websocket
from .services import redis_manager, manager, system_status

load_dotenv()

app = FastAPI(
    title="Trading Bot Dashboard API",
    version="2.0.0"
)

# CORS
origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(api.router)
app.include_router(websocket.router)

# Store global loop reference
main_loop = None

@app.on_event("startup")
async def startup_event():
    global main_loop
    print("🚀 Starting Trading Server...")
    
    main_loop = asyncio.get_running_loop()
    websocket.set_main_loop(main_loop) # Pass loop to WS router
    
    system_status["server_start_time"] = datetime.now().isoformat()
    
    # 1. Redis Connection
    connected = await redis_manager.connect()
    system_status["redis_connected"] = connected
    
    if connected:
        print("✅ Redis Connected")
        
        # Retrieve previous state
        saved_state = await redis_manager.get_state("trading:current_state")
        if saved_state:
            manager.current_state = saved_state
            print("✅ State Restored")
            
        # Callback for messages from Bot
        def bot_message_handler(message):
            try:
                system_status["bot_connected"] = True
                system_status["last_bot_message"] = datetime.now().isoformat()
                
                msg_type = message.get("type")
                payload = message.get("payload", {})
                
                # Update server memory
                manager.update_state(msg_type, payload)
                
                if main_loop and main_loop.is_running():
                    # Save to Redis and Broadcast to clients
                    asyncio.run_coroutine_threadsafe(
                        redis_manager.set_state("trading:current_state", manager.current_state),
                        main_loop
                    )
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast_json(msg_type, payload),
                        main_loop
                    )
            except Exception as e:
                print(f"Error in handler: {e}")

        # Subscription
        redis_manager.subscribe_sync("trading-bot-channel", bot_message_handler)

@app.on_event("shutdown")
async def shutdown_event():
    print("🛑 Shutting down...")
    if redis_manager.async_client:
        await redis_manager.set_state("trading:current_state", manager.current_state)
    await redis_manager.disconnect()