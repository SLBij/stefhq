from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database import get_session
from models.db import Memory, MemoryReviewItem, User
from services.memory import search_memories

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/review")
async def get_review_queue(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(MemoryReviewItem)
        .where(MemoryReviewItem.status == "pending")
        .order_by(MemoryReviewItem.created_at.desc())
        .limit(50)
    )
    items = result.scalars().all()
    return [
        {
            "id": str(i.id),
            "content": i.candidate_content,
            "workspace": i.suggested_workspace,
            "type": i.suggested_type,
            "confidence": i.confidence,
            "created_at": i.created_at.isoformat(),
        }
        for i in items
    ]


class ReviewAction(BaseModel):
    action: str  # approve | discard


@router.post("/review/{item_id}")
async def process_review_item(
    item_id: str,
    action: ReviewAction,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(MemoryReviewItem).where(MemoryReviewItem.id == item_id)
    )
    item = result.scalar_one()

    if action.action == "approve":
        memory = Memory(
            content=item.candidate_content,
            embedding=item.candidate_embedding,
            workspace=item.suggested_workspace,
            memory_type=item.suggested_type,
            confidence=item.confidence,
            auto_extracted=True,
            confirmed=True,
        )
        session.add(memory)
        item.status = "approved"
    else:
        item.status = "discarded"

    await session.commit()
    return {"status": item.status}


@router.get("/recent")
async def get_recent_memories(
    workspace: str | None = Query(None),
    minutes: int = Query(3),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    query = sa.select(Memory).where(Memory.created_at >= cutoff)
    if workspace:
        query = query.where(Memory.workspace == workspace)
    result = await session.execute(
        query.order_by(Memory.created_at.desc()).limit(20)
    )
    return [
        {
            "content": m.content,
            "workspace": m.workspace,
            "type": m.memory_type,
            "confidence": m.confidence,
            "tags": m.tags,
        }
        for m in result.scalars().all()
    ]


@router.get("/agent-name")
async def get_agent_name(
    workspace: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(Memory)
        .where(
            Memory.confirmed == True,
            Memory.workspace == workspace,
            Memory.tags.contains(["agent_name"]),
        )
        .limit(1)
    )
    memory = result.scalar_one_or_none()
    return {"name": memory.content if memory else None}


@router.get("/pinned")
async def get_pinned_memories(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(Memory)
        .where(Memory.confirmed == True, Memory.tags.contains(["pinned"]))
        .order_by(Memory.created_at.desc())
        .limit(20)
    )
    return [
        {
            "content": m.content,
            "workspace": m.workspace,
            "type": m.memory_type,
            "confidence": m.confidence,
            "tags": m.tags,
        }
        for m in result.scalars().all()
    ]


@router.get("/search")
async def search(
    q: str,
    workspace: str = "hive_mind",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    memories = await search_memories(session, q, workspace)
    return [
        {
            "id": str(m.id),
            "content": m.content,
            "workspace": m.workspace,
            "type": m.memory_type,
            "confidence": m.confidence,
        }
        for m in memories
    ]
