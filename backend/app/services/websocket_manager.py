from typing import Dict, Set, Optional, List, Any
from fastapi import WebSocket
import json
import asyncio
from datetime import datetime

class ConnectionManager:
    def __init__(self):
        # Active connections
        self.active_connections: Set[WebSocket] = set()
        
        # Lightweight current state (Only LIVE data)
        self.current_state = {
            "account": {
                "net_liquidation": 0.0,
                # Add other account fields here if needed
            },
            # No longer a list, but a single object or None
            "active_position": None, 
            
            # Latest price tick (for fast UI update)
            "latest_price": {
                "symbol": "",
                "price": 0.0,
                "change_percent": 0.0
            },
            
            # Keep only the latest logs for live console
            "logs": [],
            
            "last_update": None
        }
        
        self.stats = {
            "total_connections": 0,
            "messages_sent": 0
        }
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        self.stats["total_connections"] += 1
        # Send current state immediately to new client
        await self.send_initial_state(websocket)
        
    async def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
    
    async def send_initial_state(self, websocket: WebSocket):
        message = {
            "type": "initial-state",
            "payload": self.current_state,
            "timestamp": datetime.now().isoformat()
        }
        await self.send_personal_message(json.dumps(message, default=str), websocket)
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception:
            await self.disconnect(websocket)
    
    async def broadcast_json(self, message_type: str, payload: Any):
        """Helper to send JSON to all clients"""
        message = {
            "type": message_type,
            "payload": payload,
            "timestamp": datetime.now().isoformat()
        }
        json_str = json.dumps(message, default=str)
        
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(json_str)
                self.stats["messages_sent"] += 1
            except Exception:
                disconnected.add(connection)
        
        for conn in disconnected:
            self.active_connections.discard(conn)
    
    def update_state(self, message_type: str, payload: Dict[str, Any]):
        """
        Updates the server's internal memory.
        This method is called every time Redis receives a message from the bot.
        """
        self.current_state["last_update"] = datetime.now().isoformat()

        if message_type == "price_update":
            # Frequent ticker update
            self.current_state["latest_price"] = payload

        elif message_type == "account_update":
            # Liquidity update
            # Expected payload: {"net_liquidation": 12345.67}
            self.current_state["account"].update(payload)
            
        elif message_type == "position_update":
            # Expected payload: Complete position object with EMA and Stop
            # If payload is empty or None, it means there are no positions
            if not payload:
                self.current_state["active_position"] = None
            else:
                # If the payload is a list (from bot), take the first element
                if isinstance(payload, list) and len(payload) > 0:
                    self.current_state["active_position"] = payload[0]
                else:
                    self.current_state["active_position"] = payload
        
        elif message_type == "log":
            # Add log and keep only the last 50
            self.current_state["logs"].append(payload)
            if len(self.current_state["logs"]) > 50:
                self.current_state["logs"].pop(0)