import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from agents.router import Workspace
from database import async_session_factory
from models.db import Memory, Message, Conversation
from services.memory import search_memories


async def _pinned_memories(workspace: str) -> list[Memory]:
    async with async_session_factory() as s:
        result = await s.execute(
            sa.select(Memory)
            .where(
                Memory.confirmed == True,
                Memory.workspace == workspace,
                Memory.tags.contains(["agent_name"]),
            )
            .limit(5)
        )
        return list(result.scalars().all())


async def _recent_history(conversation_id) -> list[Message]:
    async with async_session_factory() as s:
        result = await s.execute(
            sa.select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(10)
        )
        return list(reversed(result.scalars().all()))


async def assemble_context(
    session: AsyncSession,
    message: str,
    workspace: Workspace,
    conversation: Conversation,
    entities: list[str],
) -> dict:
    # These three reads are independent — run them on separate connections concurrently
    # instead of serially, to avoid paying the EU-VPS-to-us-east-1-Neon round-trip cost
    # (~100ms each) one after another. (Can't share `session` across concurrent awaits —
    # asyncpg connections don't support concurrent operations on the same connection.)
    memories, pinned, history = await asyncio.gather(
        search_memories(session=session, query=message, workspace=workspace.value, limit=10),
        _pinned_memories(workspace.value),
        _recent_history(conversation.id),
    )

    seen_ids = {m.id for m in pinned}
    merged = pinned + [m for m in memories if m.id not in seen_ids]

    return {
        "memories": [
            {"content": m.content, "type": m.memory_type, "workspace": m.workspace}
            for m in merged
        ],
        "history": [{"role": msg.role, "content": msg.content} for msg in history if msg.content],
        "entities": entities,
        "workspace": workspace.value,
    }
