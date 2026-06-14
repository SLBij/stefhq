from arq import create_pool
from arq.connections import RedisSettings

from config import settings

_pool = None


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None
