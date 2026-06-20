from workers.arq_pool import get_pool

_KEY = "pip_briefing_paused_until"


async def get_pause_until() -> str | None:
    pool = await get_pool()
    val = await pool.get(_KEY)
    return val.decode() if val else None


async def set_pause_until(date_str: str) -> None:
    pool = await get_pool()
    await pool.set(_KEY, date_str)


async def clear_pause() -> None:
    pool = await get_pool()
    await pool.delete(_KEY)
