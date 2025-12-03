from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from datetime import datetime
import json
import asyncio
from ..services import manager, redis_manager

router = APIRouter()

# Riferimento al loop principale (verrà settato dal main)
main_loop = None

def set_main_loop(loop):
    global main_loop
    main_loop = loop

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                msg_type = message.get("type")
                
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                
                elif msg_type == "request-state":
                    await manager.send_initial_state(websocket)
                
                elif msg_type == "command":
                    # Invia comando al bot
                    command_payload = message.get("payload", {})
                    if main_loop and main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            redis_manager.publish("trading-bot-commands", command_payload),
                            main_loop
                        )
                    await websocket.send_json({"type": "command-sent", "payload": command_payload})

            except json.JSONDecodeError:
                pass # Ignora JSON brutti
            except Exception as e:
                print(f"WS Error processing message: {e}")
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)