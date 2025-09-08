import redis.asyncio as redis
from server.config import settings

rds: redis.Redis = redis.from_url(settings.REDIS_URI, encoding="utf-8", decode_responses=True)
