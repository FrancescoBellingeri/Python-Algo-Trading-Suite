import os
from dotenv import load_dotenv
from .websocket_manager import ConnectionManager
from .redis_client import RedisManager
from ..database import DatabaseHandler

load_dotenv()

# 1. WebSocket Manager instance
manager = ConnectionManager()

# 2. Redis Client instance
redis_manager = RedisManager(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=int(os.getenv("REDIS_DB", 0))
)

# 3. Database Handler instance
# Will connect automatically based on your database.py file
db_handler = DatabaseHandler()

# Global system status variables
system_status = {
    "redis_connected": False,
    "bot_connected": False,
    "last_bot_message": None,
    "server_start_time": None # Will be set in main.py
}