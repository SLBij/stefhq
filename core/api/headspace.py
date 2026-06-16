import json

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents import get_agent
from agents.router import route
from api.auth import get_current_user
from database import get_session
from models.db import Conversation, Message, User
from services.activity import log_activity
from services.context import assemble_context
from workers.arq_pool import get_pool

router = APIRouter(prefix="/headspace", tags=["headspace"])


class HeadspaceChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


@router.post("/chat")
async def headspace_chat(
    body: HeadspaceChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Non-streaming chat endpoint for HeadSpace Telegram bot."""
    routing = await route(body.message)

    # Telegram conversations are stored under 'inbox' so they appear in the Inbox UI,
    # regardless of which agent handles them.
    if body.conversation_id:
        result = await session.execute(
            sa.select(Conversation).where(Conversation.id == body.conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            conversation = Conversation(workspace="inbox")
            session.add(conversation)
            await session.flush()
    else:
        conversation = Conversation(workspace="inbox")
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
        workspace=routing.workspace,
        conversation=conversation,
        entities=routing.entities,
    )

    agent = get_agent(routing.workspace)
    full_response = ""
    async for event in agent.handle(body.message, context, session):
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
        workspace=routing.workspace.value,
        assistant_message_id=str(assistant_message.id),
    )

    try:
        preview = body.message[:120] + ("…" if len(body.message) > 120 else "")
        await log_activity(
            session, "telegram", routing.workspace.value, "chat", preview,
            {"conversation_id": str(conversation.id)},
        )
    except Exception:
        pass

    return {
        "response": full_response,
        "conversation_id": str(conversation.id),
        "workspace": routing.workspace.value,
    }
