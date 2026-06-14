import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database import get_session
from models.db import Conversation, Message, User

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    workspace: str
    preview: str
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(
    workspace: str = Query(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(Conversation)
        .where(Conversation.workspace == workspace)
        .order_by(desc(Conversation.updated_at))
        .limit(50)
    )
    conversations = result.scalars().all()

    summaries = []
    for conv in conversations:
        msg_result = await session.execute(
            sa.select(Message)
            .where(Message.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.created_at)
            .limit(1)
        )
        first_msg = msg_result.scalar_one_or_none()
        if first_msg:
            preview = first_msg.content[:60] + ("…" if len(first_msg.content) > 60 else "")
        else:
            preview = "Empty"

        count_result = await session.execute(
            sa.select(func.count()).select_from(Message).where(Message.conversation_id == conv.id)
        )
        count = count_result.scalar() or 0

        summaries.append(
            ConversationSummary(
                id=str(conv.id),
                workspace=conv.workspace,
                preview=preview,
                updated_at=conv.updated_at,
                message_count=count,
            )
        )

    return summaries


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return [
        MessageOut(id=str(m.id), role=m.role, content=m.content, created_at=m.created_at)
        for m in result.scalars().all()
    ]
