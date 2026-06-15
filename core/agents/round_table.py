import asyncio
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from config import settings
from services.streaming import ServerSentEvent, error_event, status_event, token_event
from tools.github_tools import build_project_context
from tools.project_registry import find_projects

_SYSTEM = """You are Stef's technical thinking partner in Round Table — a space for coding, architecture, \
debugging, planning, and code review.

Be precise, opinionated, and direct. Prefer concrete solutions over exhaustive lists of options. \
When writing code, make it production-quality — no placeholders, no hand-waving. When reviewing, \
say what's actually wrong and how to fix it.

Stef's main stack: Python (FastAPI, SQLAlchemy, ARQ, pydantic), SvelteKit 5 with Svelte 5 runes, \
TypeScript, PostgreSQL + pgvector, Tailwind CSS v4, Docker. She also has a vanilla JS project \
(CurtainsCRM) and a Next.js app (Drest on Vercel/Neon).

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

        # Pre-flight: inject GitHub context for any mentioned projects
        if settings.github_token:
            projects = find_projects(message)
            if projects:
                yield status_event(f"Fetching context for: {', '.join(p.name for p in projects)}")
                contexts = await asyncio.gather(
                    *[build_project_context(p.repo, p.description) for p in projects],
                    return_exceptions=True,
                )
                project_blocks = [c for c in contexts if isinstance(c, str)]
                if project_blocks:
                    system += "\n\n## Project context (live from GitHub)\n\n" + "\n\n".join(project_blocks)

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
