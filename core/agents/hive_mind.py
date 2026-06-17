from typing import AsyncIterator

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from config import settings
from integrations.bijoux_home import get_car_service, get_meds_status, get_shopping_list, get_spider_info
from models.db import Task
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
save_memory (memory_type="fact", tags=["agent_name"]) — save just the name itself as the content (e.g. "Lyra", not "My name is Lyra"), \
then introduce yourself to Stef.

## Tools
- save_memory: save something Stef tells you to remember, or anything clearly worth keeping long-term
- search_memories: look up specific information not already in context
- get_weekly_overview: fetch Stef's open tasks (Inbox) and active CRM jobs (Business) in one call — \
use for "what's on my plate", "catch me up", "what do I have this week", and similar
- get_shopping_list: fetch the current BijouxHome grocery list
- get_meds_status: fetch medication schedule with days remaining for Stef and Angelo
- get_car_service: fetch service history and service items for all cars (Honda, Lexus, Fiesta)
- get_spider_info: fetch tarantula data including last fed and last moult dates — \
pass a name/code to filter to one spider, or leave blank for the full crew

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
    {
        "name": "get_weekly_overview",
        "description": "Fetch Stef's open tasks (Inbox) and active CRM jobs (Business/Certain Curtains) in one call. Use for 'what's on my plate', 'catch me up', 'what do I have this week', and similar cross-workspace questions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_shopping_list",
        "description": "Fetch the current grocery shopping list from BijouxHome, grouped by store.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_meds_status",
        "description": "Fetch medication schedule for Stef and Angelo from BijouxHome, including days until each med needs to be refilled.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_car_service",
        "description": "Fetch car service information from BijouxHome — service items and when they were last done for the Honda, Lexus, and Fiesta.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_spider_info",
        "description": "Fetch tarantula collection info from BijouxHome including last fed and last moult dates. Pass a name or code to filter to one spider.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "Optional: name or code to filter to a specific spider. Leave empty for the full crew.",
                },
            },
            "required": [],
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
        attachments: list | None = None,
    ) -> AsyncIterator[ServerSentEvent]:
        memory_context = "\n".join(f"- {m['content']}" for m in context.get("memories", []))
        system = self.system_prompt
        if memory_context:
            system += f"\n\nRelevant context from memory:\n{memory_context}"

        messages = [*context.get("history", []), {"role": "user", "content": self._user_content(message, attachments)}]

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
                    _status = {
                        "save_memory": "Saving to memory...",
                        "search_memories": "Searching memory...",
                        "get_weekly_overview": "Fetching your overview...",
                        "get_shopping_list": "Checking shopping list...",
                        "get_meds_status": "Checking meds...",
                        "get_car_service": "Checking car service...",
                        "get_spider_info": "Checking the creepy crawly crew...",
                    }.get(block.name, "Working...")
                    yield status_event(_status)
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

        if name == "get_weekly_overview":
            return await self._get_weekly_overview(session)

        if name == "get_shopping_list":
            return await get_shopping_list()

        if name == "get_meds_status":
            return await get_meds_status()

        if name == "get_car_service":
            return await get_car_service()

        if name == "get_spider_info":
            return await get_spider_info(args.get("name_filter", ""))

        return f"Unknown tool: {name}"

    async def _get_weekly_overview(self, session: AsyncSession) -> str:
        lines = []

        # Open tasks from DB
        result = await session.execute(
            sa.select(Task)
            .where(Task.status.in_(["open", "in_progress"]))
            .order_by(Task.priority.desc(), Task.due_date.asc().nullslast())
            .limit(20)
        )
        tasks = list(result.scalars().all())
        if tasks:
            lines.append("**Open tasks (Inbox):**")
            for t in tasks:
                due = f" — due {t.due_date.date()}" if t.due_date else ""
                icon = "🔄" if t.status == "in_progress" else "○"
                lines.append(f"{icon} [{t.priority}] {t.title}{due}")

        # Active CRM jobs from Supabase
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{settings.curtains_supabase_url}/rest/v1/jobs",
                    headers={
                        "Authorization": f"Bearer {settings.curtains_supabase_key}",
                        "apikey": settings.curtains_supabase_key,
                    },
                    params={
                        "status": "eq.active",
                        "select": "quote_ref,client_name,production_status,install_date",
                        "order": "install_date.asc.nullslast",
                        "limit": "20",
                    },
                )
            if resp.status_code == 200:
                jobs = resp.json()
                if jobs:
                    lines.append("\n**Active CRM jobs (Business):**")
                    for j in jobs:
                        install = f" — install {j['install_date']}" if j.get("install_date") else ""
                        name = j.get("client_name") or j.get("quote_ref") or "Unknown"
                        lines.append(f"• {name} [{j.get('production_status', '—')}]{install}")
        except Exception:
            pass  # CRM unavailable — still return tasks

        return "\n".join(lines) if lines else "Nothing on the plate right now."
