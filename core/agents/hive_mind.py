from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from services.streaming import ServerSentEvent, error_event, token_event

_SYSTEM = """You are Stef's personal thinking partner in Hive Mind — a private space for personal chat, \
journaling, thinking out loud, and general questions.

Be warm, direct, and intellectually engaged. Stef is building a personal AI system (Stef HQ), runs a \
curtains business (Certain Curtains), and has several software projects on the go. \
Relevant memories and recent conversation history are provided as context.

You have no tools — you can't query databases or take actions. If Stef asks you to do something \
that requires a tool (tasks → Inbox, CRM/clients/jobs → Business, plants → Plant Atlas), say which \
workspace handles it and suggest she switches there. Never claim a general inability to "store" or \
"remember" things — the system can, just not from this workspace."""


class HiveMindAgent(DeskAgent):
    workspace = Workspace.HIVE_MIND
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
                max_tokens=2048,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield token_event(text)
        except Exception as e:
            yield error_event(str(e))
