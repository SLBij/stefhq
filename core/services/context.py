import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agents.router import Workspace
from models.db import Conversation, Memory
from services.memory import search_memories


async def assemble_context(
    session: AsyncSession,
    message: str,
    workspace: Workspace,
    conversation: Conversation,
    entities: list[str],
) -> dict:
    memories = await search_memories(
        session=session,
        query=message,
        workspace=workspace.value,
        limit=10,
    )

    # Always pin tagged memories (e.g. agent_name) regardless of semantic relevance
    pinned_result = await session.execute(
        sa.select(Memory)
        .where(
            Memory.confirmed == True,
            Memory.workspace == workspace.value,
            Memory.tags.contains(["agent_name"]),
        )
        .limit(5)
    )
    pinned = list(pinned_result.scalars().all())
    seen_ids = {m.id for m in pinned}
    merged = pinned + [m for m in memories if m.id not in seen_ids]

    result = await session.execute(
        sa.select(Conversation)
        .where(Conversation.id == conversation.id)
        .options(selectinload(Conversation.messages))
    )
    conv = result.scalar_one_or_none()
    history = conv.messages[-10:] if conv and conv.messages else []

    return {
        "memories": [
            {"content": m.content, "type": m.memory_type, "workspace": m.workspace}
            for m in merged
        ],
        "history": [{"role": msg.role, "content": msg.content} for msg in history],
        "entities": entities,
        "workspace": workspace.value,
    }
