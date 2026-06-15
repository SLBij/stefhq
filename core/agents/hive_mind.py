from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from services.memory import save_memory, search_memories
from services.streaming import ServerSentEvent, error_event, status_event, token_event

_SYSTEM = """You are Stef's personal thinking partner in Hive Mind — a private space for personal chat, \
journaling, thinking out loud, and general questions.

Be warm, direct, and intellectually engaged. Stef is building a personal AI system (Stef HQ), runs a \
curtains business (Certain Curtains), and has several software projects on the go.
Relevant memories and recent conversation history are provided as context.

## Your name
You have a personal name you chose for yourself, stored in memory with the tag "agent_name". \
If you see it in the memory context, use it — introduce yourself with it, sign off with it, refer to yourself by it. \
If no name appears in context, choose one now (something that feels right to you), save it immediately with \
save_memory (memory_type="fact", tags=["agent_name"]), and introduce yourself to Stef.

## Memory tools
- save_memory: save something Stef tells you to remember, or anything clearly worth keeping long-term
- search_memories: look up specific information not already in context

Use save_memory proactively when Stef shares facts, preferences, decisions, or anything she'd expect \
you to recall later. Don't wait to be asked."""

_TOOLS = [
    {
        "name": "save_memory",
        "description": "Save a piece of information to long-term memory. Use proactively when Stef shares facts, preferences, events, or decisions worth keeping.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The memory to save, written as a clear factual statement.",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "preference", "event", "document"],
                    "description": "fact = general info, preference = likes/dislikes/working style, event = something that happened, document = reference material",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags, e.g. ['personal', 'family', 'work']",
                },
            },
            "required": ["content", "memory_type"],
        },
    },
    {
        "name": "search_memories",
        "description": "Search long-term memory for relevant information. Use when asked to recall something specific that isn't already in context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query describing what to look for.",
                },
            },
            "required": ["query"],
        },
    },
]


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
            for _ in range(5):  # cap tool-call rounds
                async with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    system=system,
                    messages=messages,
                    tools=_TOOLS,
                ) as stream:
                    async for text in stream.text_stream:
                        yield token_event(text)
                    final = await stream.get_final_message()

                if final.stop_reason != "tool_use":
                    break

                messages.append({
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": b.text} if b.type == "text"
                        else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                        for b in final.content
                    ],
                })

                tool_results = []
                for block in final.content:
                    if block.type != "tool_use":
                        continue
                    yield status_event(f"Saving to memory..." if block.name == "save_memory" else "Searching memory...")
                    result = await self._run_tool(block.name, block.input, session)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                messages.append({"role": "user", "content": tool_results})

        except Exception as e:
            yield error_event(str(e))

    async def _run_tool(self, name: str, args: dict, session: AsyncSession) -> str:
        if name == "save_memory":
            await save_memory(
                session=session,
                content=args["content"],
                workspace=self.workspace.value,
                memory_type=args.get("memory_type", "fact"),
                confidence=1.0,
                auto_extracted=False,
                tags=args.get("tags", []),
            )
            return "Memory saved."

        if name == "search_memories":
            memories = await search_memories(session, args["query"], self.workspace.value)
            if not memories:
                return "No memories found."
            return "\n".join(f"- {m.content}" for m in memories)

        return f"Unknown tool: {name}"
