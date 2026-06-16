from sqlalchemy.ext.asyncio import AsyncSession

from models.db import ActivityLog


async def log_activity(
    session: AsyncSession,
    source: str,
    workspace: str,
    action_type: str,
    summary: str,
    metadata: dict | None = None,
) -> None:
    entry = ActivityLog(
        source=source,
        workspace=workspace,
        action_type=action_type,
        summary=summary,
        metadata_=metadata,
    )
    session.add(entry)
    await session.commit()
