from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import Memory, MemoryReviewItem
from services.embeddings import embed

CONFIDENCE_THRESHOLD = 0.75


async def search_memories(
    session: AsyncSession,
    query: str,
    workspace: str,
    limit: int = 10,
) -> list[Memory]:
    query_embedding = await embed(query)
    result = await session.execute(
        sa.select(Memory)
        .where(
            Memory.confirmed == True,
            Memory.workspace.in_([workspace, "global"]),
        )
        .order_by(Memory.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    memories = list(result.scalars().all())
    for m in memories:
        m.last_accessed_at = sa.func.now()
    if memories:
        await session.commit()
    return memories


async def save_memory(
    session: AsyncSession,
    content: str,
    workspace: str,
    memory_type: str,
    confidence: float = 1.0,
    auto_extracted: bool = False,
    tags: list[str] | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
) -> Memory:
    embedding = await embed(content)
    memory = Memory(
        content=content,
        embedding=embedding,
        workspace=workspace,
        memory_type=memory_type,
        confidence=confidence,
        auto_extracted=auto_extracted,
        confirmed=not auto_extracted or confidence >= CONFIDENCE_THRESHOLD,
        tags=tags or [],
        entity_type=entity_type,
        entity_id=entity_id,
    )
    session.add(memory)
    await session.commit()
    return memory


async def queue_for_review(
    session: AsyncSession,
    candidate_content: str,
    suggested_workspace: str,
    suggested_type: str,
    confidence: float,
    source_message_id: UUID | None = None,
    conflict_with_memory_id: UUID | None = None,
) -> MemoryReviewItem:
    embedding = await embed(candidate_content)
    item = MemoryReviewItem(
        candidate_content=candidate_content,
        candidate_embedding=embedding,
        suggested_workspace=suggested_workspace,
        suggested_type=suggested_type,
        confidence=confidence,
        source_message_id=source_message_id,
        conflict_with_memory_id=conflict_with_memory_id,
    )
    session.add(item)
    await session.commit()
    return item
