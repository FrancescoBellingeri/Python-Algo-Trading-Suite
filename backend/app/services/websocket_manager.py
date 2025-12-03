from typing import Dict, Set, Optional, List, Any
from fastapi import WebSocket
import json
import asyncio
from datetime import datetime

class ConnectionManager:
    def __init__(self):
        # Connessioni attive
        self.active_connections: Set[WebSocket] = set()
        
        # Stato corrente snello (Solo dati LIVE)
        self.current_state = {
            "account": {
                "net_liquidation": 0.0,
                # Aggiungi qui altri campi account se servono
            },
            # Non più una lista, ma un singolo oggetto o None
            "active_position": None, 
            
            # Ultimo prezzo battuto (per aggiornamento UI rapido)
            "latest_price": {
                "symbol": "",
                "price": 0.0,
                "change_percent": 0.0
            },
            
            # Teniamo solo gli ultimi log per la console live
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
        # Invia subito lo stato attuale al nuovo client
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
        """Helper per inviare JSON a tutti"""
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
        Aggiorna la memoria interna del server.
        Questo metodo viene chiamato ogni volta che Redis riceve un messaggio dal bot.
        """
        self.current_state["last_update"] = datetime.now().isoformat()

        if message_type == "price_update":
            # Aggiornamento frequente del ticker
            self.current_state["latest_price"] = payload

        elif message_type == "account_update":
            # Aggiornamento liquidità
            # Payload atteso: {"net_liquidation": 12345.67}
            self.current_state["account"].update(payload)
            
        elif message_type == "position_update":
            # Payload atteso: Oggetto posizione completo con EMA e Stop
            # Se il payload è vuoto o None, significa che non ci sono posizioni
            if not payload:
                self.current_state["active_position"] = None
            else:
                self.current_state["active_position"] = payload
        
        elif message_type == "log":
            # Aggiungi log e mantieni solo gli ultimi 50
            self.current_state["logs"].append(payload)
            if len(self.current_state["logs"]) > 50:
                self.current_state["logs"].pop(0)