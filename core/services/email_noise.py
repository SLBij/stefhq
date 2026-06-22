from workers.arq_pool import get_pool

_KEY = "pip_email_noise_patterns"


async def add_noise_pattern(pattern: str) -> None:
    pool = await get_pool()
    await pool.sadd(_KEY, pattern)


async def remove_noise_pattern(pattern: str) -> bool:
    pool = await get_pool()
    removed = await pool.srem(_KEY, pattern)
    return bool(removed)


async def list_noise_patterns() -> list[str]:
    pool = await get_pool()
    members = await pool.smembers(_KEY)
    return sorted(m.decode() if isinstance(m, bytes) else m for m in members)
