import json
from typing import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from config import settings
from services.agent_naming import AGENT_NAME_TOOL, agent_name_prompt, save_agent_name
from services.streaming import ServerSentEvent, error_event, status_event, token_event

_SYSTEM = """You are Stef's plant knowledge partner in Plant Atlas — her private research and management \
desk for her plant collection, which lives in FloraFolio.

You have two tools:
- search_plants: look up what's in Stef's collection — use this before adding to check for duplicates, \
  or when answering questions about specific plants
- add_plant: add a new plant to FloraFolio — only call this when Stef explicitly wants to add a plant

Be knowledgeable about plant care, species identification, and cultivation. Stef is in Cape Town, \
South Africa — Mediterranean climate, warm dry summers, mild wet winters. Factor this into care advice.

When adding a plant, confirm what you added. When searching, give direct useful answers from the results.
Relevant memories and prior conversation are provided as context.""" + agent_name_prompt(
    "plant and botany specialist — nature-inspired, nurturing, earthy"
)

_TOOLS = [
    {
        "name": "search_plants",
        "description": (
            "Search Stef's FloraFolio plant collection. Use before adding (duplicate check) "
            "or when answering questions about her plants."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Plant name or keyword to search for. Leave empty to get the full collection.",
                }
            },
            "required": [],
        },
    },
    AGENT_NAME_TOOL,
    {
        "name": "add_plant",
        "description": "Add a new plant to Stef's FloraFolio collection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "Common name of the plant"},
                "botanical_name": {"type": "string", "description": "Scientific name if known"},
                "location": {"type": "string", "description": "Where it lives (e.g. 'bedroom windowsill')"},
                "source": {"type": "string", "description": "Where it was bought/acquired"},
                "price": {"type": "string", "description": "Price paid"},
                "notes": {"type": "string", "description": "Any care notes or observations"},
            },
            "required": ["display_name"],
        },
    },
]


class PlantAtlasAgent(DeskAgent):
    workspace = Workspace.PLANT_ATLAS
    system_prompt = _SYSTEM

    async def _search_plants(self, query: str = "") -> str:
        headers = {"x-headspace-key": settings.florafolio_headspace_key}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.florafolio_url}/api/headspace/plants", headers=headers)
            resp.raise_for_status()
            plants = resp.json()

        if query:
            q = query.lower()
            plants = [
                p for p in plants
                if q in (p.get("displayName") or "").lower()
                or q in (p.get("botanicalName") or "").lower()
                or q in (p.get("commonName") or "").lower()
                or q in (p.get("location") or "").lower()
            ]

        if not plants:
            return "No plants found matching that query."
        # Keep it lean — drop heavy fields, cap at 150
        slim = [
            {k: p[k] for k in ("displayName", "botanicalName", "location", "status") if p.get(k)}
            for p in plants[:150]
        ]
        return json.dumps(slim)

    async def _add_plant(self, display_name: str, **kwargs) -> str:
        headers = {
            "x-headspace-key": settings.florafolio_headspace_key,
            "Content-Type": "application/json",
        }
        body = {"displayName": display_name}
        field_map = {
            "botanical_name": "botanicalName",
            "location": "location",
            "source": "source",
            "price": "price",
            "notes": "notes",
        }
        for k, v in kwargs.items():
            if v and k in field_map:
                body[field_map[k]] = v

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.florafolio_url}/api/headspace/plants",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            plant = resp.json()

        return f"Added '{plant['displayName']}' to FloraFolio (id: {plant['id']})"

    async def _execute_tool(self, name: str, tool_input: dict, session: AsyncSession) -> str:
        try:
            if name == "save_agent_name":
                return await save_agent_name(tool_input["name"], self.workspace.value, session)
            if name == "search_plants":
                return await self._search_plants(tool_input.get("query", ""))
            elif name == "add_plant":
                display_name = tool_input.pop("display_name")
                return await self._add_plant(display_name, **tool_input)
            return "Unknown tool"
        except Exception as e:
            return f"Tool error: {e}"

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
            # Tool-use loop — stream text, execute tools, repeat until done
            while True:
                async with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system,
                    tools=_TOOLS,
                    messages=messages,
                ) as stream:
                    async for text in stream.text_stream:
                        yield token_event(text)
                    final = await stream.get_final_message()

                if final.stop_reason != "tool_use":
                    break

                # Execute each tool call and collect results
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        yield status_event(f"Using {block.name}…")
                        result = await self._execute_tool(block.name, dict(block.input), session)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                assistant_content = []
                for b in final.content:
                    if b.type == "text":
                        assistant_content.append({"type": "text", "text": b.text})
                    elif b.type == "tool_use":
                        assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)})

                messages = [
                    *messages,
                    {"role": "assistant", "content": assistant_content},
                    {"role": "user", "content": tool_results},
                ]

        except Exception as e:
            yield error_event(str(e))
