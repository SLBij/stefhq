import json
import uuid

from workers.arq_pool import get_pool

_TTL = 86400  # 24 hours


async def _store(prefix: str, details: dict) -> str:
    pending_id = str(uuid.uuid4())
    pool = await get_pool()
    await pool.set(f"{prefix}{pending_id}", json.dumps(details), ex=_TTL)
    return pending_id


async def _pop(prefix: str, pending_id: str) -> dict | None:
    pool = await get_pool()
    key = f"{prefix}{pending_id}"
    raw = await pool.get(key)
    if raw is None:
        return None
    await pool.delete(key)
    return json.loads(raw)


async def store_pending_event(details: dict) -> str:
    return await _store("pending_event:", details)


async def pop_pending_event(pending_id: str) -> dict | None:
    return await _pop("pending_event:", pending_id)


async def store_pending_email(details: dict) -> str:
    return await _store("pending_email:", details)


async def pop_pending_email(pending_id: str) -> dict | None:
    return await _pop("pending_email:", pending_id)
