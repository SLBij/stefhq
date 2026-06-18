import json

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from agents import get_agent
from agents.router import IntentType, RoutingDecision, Workspace, route
from api.auth import get_current_user
from database import get_session
from models.db import Conversation, Message, User
from services.context import assemble_context
from services.activity import log_activity
from services.streaming import done_event, error_event, status_event
from services.title import generate_title
from workers.arq_pool import get_pool

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    workspace: str | None = None
    conversation_id: str | None = None
    attachments: list[dict] | None = None


@router.post("/")
async def chat(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    async def stream():
        try:
            current_ws = Workspace(request.workspace) if request.workspace else None
            if current_ws:
                # Client sent an explicit workspace — trust it, no LLM routing needed
                routing = RoutingDecision(
                    workspace=current_ws,
                    intent_type=IntentType.QUESTION,
                    entities=[],
                    reasoning="workspace set by client",
                )
            else:
                routing = await route(request.message, current_ws, request.attachments)
            yield status_event(f"Routing to {routing.workspace}")

            is_new_conversation = not request.conversation_id
            if request.conversation_id:
                result = await session.execute(
                    sa.select(Conversation).where(Conversation.id == request.conversation_id)
                )
                conversation = result.scalar_one()
            else:
                conversation = Conversation(workspace=routing.workspace.value)
                session.add(conversation)
                await session.flush()

            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content=request.message,
                attachments=request.attachments,
            )
            session.add(user_message)
            await session.flush()

            context = await assemble_context(
                session=session,
                message=request.message,
                workspace=routing.workspace,
                conversation=conversation,
                entities=routing.entities,
            )
            context["user_id"] = user.id
            from datetime import datetime, timezone, timedelta
            _SAST = timezone(timedelta(hours=2))
            context["current_datetime"] = datetime.now(_SAST).strftime("%A, %d %B %Y at %H:%M SAST")
            yield status_event("Context assembled")

            agent = get_agent(routing.workspace)
            full_response = ""
            async for event in agent.handle(request.message, context, session, request.attachments):
                if event.event == "token":
                    full_response += json.loads(event.data).get("content", "")
                yield event

            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_response,
            )
            session.add(assistant_message)
            await session.commit()

            yield done_event(str(assistant_message.id), str(conversation.id))

            try:
                preview = request.message[:120] + ("…" if len(request.message) > 120 else "")
                await log_activity(
                    session, "web", routing.workspace.value, "chat", preview,
                    {"conversation_id": str(conversation.id)},
                )
            except Exception:
                pass

            if is_new_conversation:
                try:
                    title = await generate_title(request.message, full_response)
                    conversation.title = title
                    await session.commit()
                except Exception:
                    pass  # title is cosmetic — never fail the request over it

            if request.message.strip():
                pool = await get_pool()
                await pool.enqueue_job(
                    "extract_memories",
                    user_message=request.message,
                    assistant_message=full_response,
                    workspace=routing.workspace.value,
                    assistant_message_id=str(assistant_message.id),
                )

        except Exception as e:
            yield error_event(str(e))

    return EventSourceResponse(stream())
