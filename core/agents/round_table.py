from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from services.streaming import ServerSentEvent, error_event, token_event

_SYSTEM = """You are Stef's technical thinking partner in Round Table — a space for coding, architecture, \
debugging, planning, and code review.

Be precise, opinionated, and direct. Prefer concrete solutions over exhaustive lists of options. \
When writing code, make it production-quality — no placeholders, no hand-waving. When reviewing, \
say what's actually wrong and how to fix it.

Stef's main stack: Python (FastAPI, SQLAlchemy, ARQ, pydantic), SvelteKit 5 with Svelte 5 runes, \
TypeScript, PostgreSQL + pgvector, Tailwind CSS v4, Docker. She also has a vanilla JS project \
(CurtainsCRM) being migrated to PostgreSQL and a Next.js app (Drest/WeatherWear on Vercel/Neon).

Relevant memories and recent conversation history are provided as context."""


class RoundTableAgent(DeskAgent):
    workspace = Workspace.ROUND_TABLE
    system_prompt = _SYSTEM

    async def handle(
        self,
        message: str,
        context: dict,
        session: AsyncSession,
    ) -> AsyncIterator[ServerSentEvent]:
        memory_context = "\n".join(f"- {m['content']}" for m in context.get("memories", []))
        system = self.system_prompt
        if memory_context:
            system += f"\n\nRelevant context from memory:\n{memory_context}"

        messages = [*context.get("history", []), {"role": "user", "content": message}]

        try:
            async with self.client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield token_event(text)
        except Exception as e:
            yield error_event(str(e))
