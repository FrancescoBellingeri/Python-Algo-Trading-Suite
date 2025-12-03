import os
from dotenv import load_dotenv
from .websocket_manager import ConnectionManager
from .redis_client import RedisManager
from ..database import DatabaseHandler

load_dotenv()

# 1. Istanza del WebSocket Manager
manager = ConnectionManager()

# 2. Istanza del Redis Client
redis_manager = RedisManager(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=int(os.getenv("REDIS_DB", 0))
)

# 3. Istanza del Database Handler
# Si collegherà automaticamente in base al tuo file database.py
db_handler = DatabaseHandler()

# Variabili globali di stato sistema
system_status = {
    "redis_connected": False,
    "bot_connected": False,
    "last_bot_message": None,
    "server_start_time": None # Verrà settato in main.py
}