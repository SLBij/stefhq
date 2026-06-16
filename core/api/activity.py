from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database import get_session
from models.db import ActivityLog, User

router = APIRouter(prefix="/activity", tags=["activity"])


class ActivityEntry(BaseModel):
    id: str
    created_at: datetime
    source: str
    workspace: str
    action_type: str
    summary: str


@router.get("/", response_model=list[ActivityEntry])
async def list_activity(
    limit: int = Query(100, le=500),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(ActivityLog)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        ActivityEntry(
            id=str(log.id),
            created_at=log.created_at,
            source=log.source,
            workspace=log.workspace,
            action_type=log.action_type,
            summary=log.summary,
        )
        for log in logs
    ]
