import redis
import redis.asyncio as aioredis
import json
import asyncio
from typing import Optional, Callable

class RedisManager:
    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0):
        self.host = host
        self.port = port
        self.db = db
        self.async_client: Optional[aioredis.Redis] = None
        self.sync_client: Optional[redis.Redis] = None  # Use normal redis, not asyncio
        self.pubsub: Optional[redis.client.PubSub] = None
        self.subscriber_thread = None

    async def connect(self):
        """Initializes async and sync Redis connections"""
        try:
            # Async client for async operations
            self.async_client = aioredis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True
            )

            # Test ping async
            await self.async_client.ping()

            # Normal SYNC client (NOT asyncio) for pubsub
            self.sync_client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True
            )

            # Test ping sync
            self.sync_client.ping()

            print(f"✅ Connected to Redis at {self.host}:{self.port}")
            return True

        except Exception as e:
            print(f"❌ Failed to connect to Redis: {e}")
            return False

    async def disconnect(self):
        """Closes Redis connections"""
        if self.subscriber_thread and self.subscriber_thread.is_alive():
            # We cannot stop the thread directly, but being daemon it will stop
            pass

        if self.pubsub:
            self.pubsub.close()

        if self.async_client:
            await self.async_client.close()

        if self.sync_client:
            self.sync_client.close()

    async def get_state(self, key: str) -> Optional[dict]:
        """Retrieves saved state from Redis"""
        try:
            data = await self.async_client.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            print(f"Error getting state from Redis: {e}")
            return None

    async def set_state(self, key: str, value: dict, expire: int = None):
        """Saves state to Redis"""
        try:
            json_data = json.dumps(value, default=str)
            if expire:
                await self.async_client.setex(key, expire, json_data)
            else:
                await self.async_client.set(key, json_data)
            return True
        except Exception as e:
            print(f"Error setting state in Redis: {e}")
            return False

    async def publish(self, channel: str, message: dict):
        """Publishes message to Redis channel"""
        try:
            json_message = json.dumps(message, default=str)
            await self.async_client.publish(channel, json_message)
            return True
        except Exception as e:
            print(f"Error publishing to Redis: {e}")
            return False

    def subscribe_sync(self, channel: str, callback: Callable):
        """Synchronous subscription to receive messages from bot"""
        if not self.sync_client:
            print("❌ Sync client not initialized")
            return None
            
        self.pubsub = self.sync_client.pubsub()
        self.pubsub.subscribe(channel)

        def listen():
            try:
                # Ignore the first message which is the subscription confirmation
                for message in self.pubsub.listen():
                    if message['type'] == 'message':
                        try:
                            data = json.loads(message['data'])
                            callback(data)
                        except json.JSONDecodeError as e:
                            print(f"Error decoding message: {e}")
                        except Exception as e:
                            print(f"Error processing message: {e}")
            except Exception as e:
                print(f"Error in listen thread: {e}")

        # Start in separate thread
        import threading
        thread = threading.Thread(target=listen, daemon=True, name="Redis-Subscriber")
        thread.start()
        self.subscriber_thread = thread
        return thread