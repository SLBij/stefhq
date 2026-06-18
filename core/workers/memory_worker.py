"""
ARQ memory extraction worker.
Run with: uv run arq workers.memory_worker.WorkerSettings
"""
from uuid import UUID

from arq import cron
from arq.connections import RedisSettings

from config import settings
from database import async_session_factory
from services.memory import CONFIDENCE_THRESHOLD, queue_for_review, save_memory
from services.memory_extractor import extract_from_exchange
from workers.briefing_worker import send_morning_briefing, send_telegram_reminder


async def extract_memories(
    ctx,
    user_message: str,
    assistant_message: str,
    workspace: str,
    assistant_message_id: str | None = None,
):
    candidates = await extract_from_exchange(user_message, assistant_message, workspace)
    if not candidates:
        return {"saved": 0, "queued": 0}

    saved = queued = 0
    async with async_session_factory() as session:
        for candidate in candidates:
            if candidate.confidence >= CONFIDENCE_THRESHOLD:
                await save_memory(
                    session=session,
                    content=candidate.content,
                    workspace=candidate.workspace,
                    memory_type=candidate.memory_type,
                    confidence=candidate.confidence,
                    auto_extracted=True,
                    tags=candidate.tags,
                )
                saved += 1
            else:
                await queue_for_review(
                    session=session,
                    candidate_content=candidate.content,
                    suggested_workspace=candidate.workspace,
                    suggested_type=candidate.memory_type,
                    confidence=candidate.confidence,
                    source_message_id=UUID(assistant_message_id) if assistant_message_id else None,
                )
                queued += 1

    return {"saved": saved, "queued": queued}


class WorkerSettings:
    functions = [extract_memories, send_morning_briefing, send_telegram_reminder]
    cron_jobs = [
        cron(send_morning_briefing, hour=6, minute=15),  # 8:15am SAST (UTC+2)
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
