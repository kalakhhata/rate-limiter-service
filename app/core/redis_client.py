import redis.asyncio as aioredis
from app.core.config import settings


class RedisClient:
    def __init__(self):
        self._client: aioredis.Redis | None = None

    async def connect(self):
        self._client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

    async def disconnect(self):
        if self._client:
            await self._client.aclose()

    async def ping(self) -> bool:
        try:
            return await self._client.ping()
        except Exception:
            return False

    def get_client(self) -> aioredis.Redis:
        if not self._client:
            raise RuntimeError("Redis client not initialized. Call connect() first.")
        return self._client


redis_client = RedisClient()


def get_redis() -> aioredis.Redis:
    return redis_client.get_client()
