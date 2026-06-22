import json

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business import BusinessAgent
from api.auth import get_current_user
from database import get_session
from models.db import Conversation, Message, User
from services.activity import log_activity
from services.context import assemble_context
from workers.arq_pool import get_pool

router = APIRouter(prefix="/pip", tags=["pip"])

_agent = BusinessAgent()


class ImageAttachment(BaseModel):
    type: str = "image"
    media_type: str = "image/jpeg"
    data: str  # base64-encoded


class PipChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    images: list[ImageAttachment] = []


@router.post("/chat")
async def pip_chat(
    body: PipChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Non-streaming chat endpoint for Pip Telegram bot — routes directly to BusinessAgent."""
    from agents.router import Workspace

    if body.conversation_id:
        result = await session.execute(
            sa.select(Conversation).where(Conversation.id == body.conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            conversation = Conversation(workspace="business")
            session.add(conversation)
            await session.flush()
    else:
        conversation = Conversation(workspace="business")
        session.add(conversation)
        await session.flush()

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=body.message,
    )
    session.add(user_message)
    await session.flush()

    context = await assemble_context(
        session=session,
        message=body.message,
        workspace=Workspace.BUSINESS,
        conversation=conversation,
        entities=[],
    )
    from services.datetime_context import format_current_datetime
    context["current_datetime"] = format_current_datetime()
    context["user_id"] = user.id

    attachments = [{"type": a.type, "media_type": a.media_type, "data": a.data} for a in body.images] or None

    full_response = ""
    async for event in _agent.handle(body.message, context, session, attachments=attachments):
        if event.event == "token":
            full_response += json.loads(event.data).get("content", "")

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=full_response,
    )
    session.add(assistant_message)
    await session.commit()

    pool = await get_pool()
    await pool.enqueue_job(
        "extract_memories",
        user_message=body.message,
        assistant_message=full_response,
        workspace="business",
        assistant_message_id=str(assistant_message.id),
    )

    try:
        preview = body.message[:120] + ("…" if len(body.message) > 120 else "")
        await log_activity(
            session, "telegram", "business", "chat", preview,
            {"conversation_id": str(conversation.id)},
        )
    except Exception:
        pass

    return {
        "response": full_response,
        "conversation_id": str(conversation.id),
        "workspace": "business",
    }
