import redis
import redis.asyncio as aioredis
import json
import threading
import logging
from typing import Optional, Callable, Dict, Any

# Configure logger for this module
logger = logging.getLogger("redis_client")

class RedisManager:
    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0):
        self.host = host
        self.port = port
        self.db = db
        
        # Async client for REST API operations (Get/Set state)
        self.async_client: Optional[aioredis.Redis] = None
        
        # Sync client for listening thread (PubSub)
        self.sync_client: Optional[redis.Redis] = None
        
        self.pubsub = None
        self.subscriber_thread: Optional[threading.Thread] = None
        self._is_running = False

    async def connect(self) -> bool:
        """Initialize Redis connections (Async and Sync)"""
        try:
            # 1. Async Connection
            self.async_client = aioredis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True,
                socket_timeout=5.0
            )
            await self.async_client.ping()

            # 2. Sync Connection (for PubSub Thread)
            self.sync_client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True,
                socket_timeout=5.0
            )
            self.sync_client.ping()

            logger.info(f"✅ Connected to Redis at {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
            return False

    async def disconnect(self):
        """Closes all connections cleanly"""
        self._is_running = False
        
        # Close PubSub
        if self.pubsub:
            try:
                self.pubsub.close()
            except Exception:
                pass

        # Close Async client
        if self.async_client:
            await self.async_client.close()

        # Close Sync client
        if self.sync_client:
            self.sync_client.close()
            
        logger.info("Redis connections closed")

    async def get_state(self, key: str) -> Optional[Dict]:
        """Retrieve saved state (Async)"""
        if not self.async_client:
            return None
        try:
            data = await self.async_client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f"Error getting state: {e}")
            return None

    async def set_state(self, key: str, value: Dict, expire: int = None) -> bool:
        """Save state (Async)"""
        if not self.async_client:
            return False
        try:
            json_data = json.dumps(value, default=str)
            if expire:
                await self.async_client.setex(key, expire, json_data)
            else:
                await self.async_client.set(key, json_data)
            return True
        except Exception as e:
            logger.error(f"Error setting state: {e}")
            return False

    async def publish(self, channel: str, message: Dict) -> bool:
        """Publish message to a channel (Async)"""
        if not self.async_client:
            return False
        try:
            json_message = json.dumps(message, default=str)
            await self.async_client.publish(channel, json_message)
            return True
        except Exception as e:
            logger.error(f"Error publishing: {e}")
            return False

    def subscribe_sync(self, channel: str, callback: Callable[[Dict], None]):
        """
        Starts a separate thread that listens to Redis messages
        and invokes the callback when it receives one.
        """
        if not self.sync_client:
            logger.error("Sync client not initialized")
            return

        self._is_running = True
        self.pubsub = self.sync_client.pubsub()
        self.pubsub.subscribe(channel)

        def _listen_loop():
            logger.info(f"🎧 Redis Listener started on channel: {channel}")
            
            # Infinite listening loop
            while self._is_running:
                try:
                    # listen() is a blocking generator, but with internal timeout
                    for message in self.pubsub.listen():
                        if not self._is_running:
                            break
                            
                        if message['type'] == 'message':
                            try:
                                payload = json.loads(message['data'])
                                callback(payload)
                            except json.JSONDecodeError:
                                logger.warning(f"Invalid JSON received: {message['data']}")
                            except Exception as e:
                                logger.error(f"Error processing message callback: {e}")
                                
                except Exception as e:
                    if self._is_running:
                        logger.error(f"Error in Redis listener loop: {e}")
                        # Brief pause to avoid error spam if Redis is down
                        import time
                        time.sleep(2)
                    else:
                        break
            
            logger.info("🎧 Redis Listener stopped")

        # Start daemon thread (closes when main process dies)
        self.subscriber_thread = threading.Thread(
            target=_listen_loop, 
            daemon=True, 
            name="Redis-Subscriber-Thread"
        )
        self.subscriber_thread.start()