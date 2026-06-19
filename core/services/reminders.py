import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import Reminder


async def create_reminder(
    session: AsyncSession,
    message: str,
    remind_at_utc: datetime,
    workspace: str,
) -> Reminder:
    reminder = Reminder(message=message, remind_at=remind_at_utc, workspace=workspace)
    session.add(reminder)
    await session.flush()
    return reminder


async def set_arq_job_id(session: AsyncSession, reminder: Reminder, job_id: str) -> None:
    reminder.arq_job_id = job_id
    await session.commit()


async def list_pending(session: AsyncSession, workspace: str) -> list[Reminder]:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        sa.select(Reminder)
        .where(Reminder.workspace == workspace)
        .where(Reminder.fired.is_(False))
        .where(Reminder.remind_at > now)
        .order_by(Reminder.remind_at.asc())
    )
    return list(result.scalars().all())


async def get_by_id(session: AsyncSession, reminder_id: str) -> Reminder | None:
    try:
        rid = uuid.UUID(reminder_id)
    except ValueError:
        return None
    result = await session.execute(sa.select(Reminder).where(Reminder.id == rid))
    return result.scalar_one_or_none()


async def cancel(session: AsyncSession, reminder_id: str) -> bool:
    reminder = await get_by_id(session, reminder_id)
    if not reminder:
        return False
    if reminder.arq_job_id:
        from arq.constants import job_key_prefix
        from workers.arq_pool import get_pool
        pool = await get_pool()
        await pool.delete(f"{job_key_prefix}{reminder.arq_job_id}")
        await pool.zrem("arq:queue", reminder.arq_job_id)
    await session.delete(reminder)
    await session.commit()
    return True


async def mark_fired(session: AsyncSession, reminder_id: str) -> None:
    try:
        rid = uuid.UUID(reminder_id)
    except ValueError:
        return
    await session.execute(
        sa.update(Reminder).where(Reminder.id == rid).values(fired=True)
    )
    await session.commit()
