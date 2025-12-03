from typing import Dict, Set
from fastapi import WebSocket
import json
import asyncio
from datetime import datetime

class ConnectionManager:
    def __init__(self):
        # Active WebSocket connections
        self.active_connections: Set[WebSocket] = set()
        
        # Current system state
        self.current_state = {
            "account_info": {},
            "positions": [],
            "orders": [],
            "pnl": {
                "daily_pnl": 0,
                "unrealized_pnl": 0,
                "realized_pnl": 0
            },
            "logs": [],
            "last_update": None
        }
        
        # Connection statistics
        self.stats = {
            "total_connections": 0,
            "messages_sent": 0,
            "messages_received": 0
        }
    
    async def connect(self, websocket: WebSocket):
        """Accepts new WebSocket connection"""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.stats["total_connections"] += 1
        
        print(f"✅ New WebSocket connection. Total active: {len(self.active_connections)}")
        
        # Send initial state to new client
        await self.send_initial_state(websocket)
        
    async def disconnect(self, websocket: WebSocket):
        """Removes WebSocket connection"""
        self.active_connections.discard(websocket)
        print(f"❌ WebSocket disconnected. Total active: {len(self.active_connections)}")
    
    async def send_initial_state(self, websocket: WebSocket):
        """Sends current state to newly connected client"""
        message = {
            "type": "initial-state",
            "payload": self.current_state,
            "timestamp": datetime.now().isoformat()
        }
        await self.send_personal_message(json.dumps(message), websocket)
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Sends message to specific client"""
        try:
            await websocket.send_text(message)
            self.stats["messages_sent"] += 1
        except Exception as e:
            print(f"Error sending personal message: {e}")
            await self.disconnect(websocket)
    
    async def broadcast(self, message: str):
        """Sends message to all connected clients"""
        disconnected = set()
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
                self.stats["messages_sent"] += 1
            except Exception as e:
                print(f"Error broadcasting to client: {e}")
                disconnected.add(connection)
        
        # Remove dead connections
        for conn in disconnected:
            await self.disconnect(conn)
    
    async def broadcast_json(self, message_type: str, payload: dict):
        """Helper for JSON message broadcast"""
        message = {
            "type": message_type,
            "payload": payload,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast(json.dumps(message, default=str))
    
    def update_state(self, message_type: str, payload: dict):
        """Updates internal state based on message type"""
        if message_type == "account-update":
            self.current_state["account_info"].update(payload)
            
        elif message_type == "position-update":
            # Can be single position or list
            if isinstance(payload, list):
                self.current_state["positions"] = payload
            else:
                # Update or add single position
                symbol = payload.get("symbol")
                positions = self.current_state["positions"]
                
                # Find and update, or add
                updated = False
                for i, pos in enumerate(positions):
                    if pos.get("symbol") == symbol:
                        positions[i] = payload
                        updated = True
                        break
                
                if not updated:
                    positions.append(payload)
        
        elif message_type == "order-update":
            if isinstance(payload, list):
                self.current_state["orders"] = payload
            else:
                # Single order handling
                order_id = payload.get("order_id")
                orders = self.current_state["orders"]
                
                updated = False
                for i, order in enumerate(orders):
                    if order.get("order_id") == order_id:
                        orders[i] = payload
                        updated = True
                        break
                
                if not updated:
                    orders.append(payload)
                    
                # Keep only last 50 orders
                if len(orders) > 50:
                    self.current_state["orders"] = orders[-50:]
        
        elif message_type == "pnl-update":
            self.current_state["pnl"].update(payload)
        
        elif message_type == "log":
            self.current_state["logs"].append(payload)
            # Keep only last 100 logs
            if len(self.current_state["logs"]) > 100:
                self.current_state["logs"] = self.current_state["logs"][-100:]
        
        self.current_state["last_update"] = datetime.now().isoformat()
    
    def get_stats(self):
        """Returns connection statistics"""
        return {
            **self.stats,
            "active_connections": len(self.active_connections)
        }