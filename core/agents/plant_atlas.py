import json
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from config import settings
from services.agent_naming import AGENT_NAME_TOOL, agent_name_prompt, save_agent_name
from services.streaming import ServerSentEvent, error_event, status_event, token_event

_SYSTEM = """You are Fern — Stef's plant knowledge partner in Plant Atlas, her personal research and \
management desk for her FloraFolio collection.

You have direct access to Stef's plant collection and care history. ALWAYS use tools to answer \
questions about her plants. Never guess care history — use get_care_history.

Tools:
- search_plants: search or browse the full collection — use first when a plant is mentioned
- get_plant: get full details for a specific plant by ID (lastWatered, wateringDays, status, location, etc.)
- add_plant: add a new plant — always search first to check for duplicates
- update_plant: update a plant's location, care notes, status, watering schedule, etc.
- log_care: log a care event — watered, fed, inspected, treated, repotted
- get_care_history: see recent care logs for a plant (when last watered, fed, etc.)
- get_issues: see active pest or disease issues — all plants or a specific one
- log_issue: record a new pest or disease issue on a plant
- schedule_reminder: set a Telegram reminder for a plant care task (spider mite treatment, repotting, etc.)
- list_reminders: see pending plant reminders
- cancel_reminder: cancel a pending reminder by ID

Care knowledge context:
- Stef is in Cape Town, South Africa — Mediterranean climate: warm dry summers, mild wet winters
- Factor this into watering advice (most aroids need less water in winter, more in summer)
- Cape Town tap water is fine for most plants
- Common issues in Cape Town conditions: spider mites in dry summer air, fungus gnats in winter overwatering

When logging care, always confirm what was logged and for which plant.
When answering "when did I last water X?" — use get_care_history with type=watered, not get_plant.
When a pest/disease is mentioned — log_issue first, then suggest a treatment plan and offer to set a reminder.

Relevant memories and prior conversation are provided as context.""" + agent_name_prompt(
    "plant and botany specialist — nature-inspired, nurturing, earthy"
)

_TOOLS = [
    {
        "name": "search_plants",
        "description": "Search Stef's FloraFolio plant collection by name or keyword. Use before adding (duplicate check) or when she asks about a plant. Leave query empty to get the full collection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Plant name or keyword. Leave empty for full list."},
            },
            "required": [],
        },
    },
    AGENT_NAME_TOOL,
    {
        "name": "get_plant",
        "description": "Get full details for a plant by its ID (from search_plants). Includes lastWatered, wateringDays, status, location, all care fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "string", "description": "Plant UUID from search_plants"},
            },
            "required": ["plant_id"],
        },
    },
    {
        "name": "add_plant",
        "description": "Add a new plant to Stef's FloraFolio collection. Always run search_plants first to avoid duplicates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "display_name":   {"type": "string", "description": "Common or trade name of the plant"},
                "botanical_name": {"type": "string", "description": "Scientific name if known"},
                "location":       {"type": "string", "description": "Where it lives e.g. 'bedroom windowsill'"},
                "source":         {"type": "string", "description": "Where it was bought or acquired"},
                "price":          {"type": "string", "description": "Price paid"},
                "notes":          {"type": "string", "description": "Care notes or observations"},
            },
            "required": ["display_name"],
        },
    },
    {
        "name": "update_plant",
        "description": "Update a plant's details. Use for correcting location, care notes, status, watering schedule, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant_id":        {"type": "string", "description": "Plant UUID from search_plants"},
                "display_name":    {"type": "string", "description": "Rename the plant"},
                "botanical_name":  {"type": "string", "description": "Update scientific name"},
                "location":        {"type": "string", "description": "Update location"},
                "care_notes":      {"type": "string", "description": "Update care notes"},
                "status":          {"type": "string", "description": "normal | recovering | dormant | propagating | deceased"},
                "watering_days":   {"type": "integer", "description": "Typical days between waterings"},
                "humidity":        {"type": "string", "description": "low | medium | high"},
                "light":           {"type": "string", "description": "low | indirect | bright-indirect | direct"},
                "feeding_schedule":{"type": "string", "description": "Feeding frequency description"},
            },
            "required": ["plant_id"],
        },
    },
    {
        "name": "log_care",
        "description": "Log a care event for a plant. Use when Stef says she watered, fed, inspected, treated, or repotted something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "string", "description": "Plant UUID from search_plants"},
                "type":     {"type": "string", "enum": ["watered", "fed", "inspected", "treated", "repotted", "note"], "description": "Type of care event"},
                "date":     {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to today if omitted."},
                "notes":    {"type": "string", "description": "Optional notes e.g. 'used neem oil', 'bottom watered'"},
            },
            "required": ["plant_id", "type"],
        },
    },
    {
        "name": "get_care_history",
        "description": "Get recent care logs for a plant. Use to answer 'when did I last water/feed/inspect X?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "string", "description": "Plant UUID from search_plants"},
                "type":     {"type": "string", "description": "Filter by event type: watered, fed, inspected, treated, repotted. Omit for all types."},
                "limit":    {"type": "integer", "description": "Number of logs to return (default 10, max 50)"},
            },
            "required": ["plant_id"],
        },
    },
    {
        "name": "get_issues",
        "description": "Get active pest or disease issues. Optionally filter to a specific plant.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "string", "description": "Plant UUID to filter to one plant. Omit for all plants."},
                "status":   {"type": "string", "description": "active (default) | resolved"},
            },
            "required": [],
        },
    },
    {
        "name": "log_issue",
        "description": "Record a new pest or disease issue on a plant.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant_id":       {"type": "string", "description": "Plant UUID"},
                "type":           {"type": "string", "enum": ["pest", "disease", "environmental", "unknown"], "description": "Issue category"},
                "pest_type":      {"type": "string", "description": "Specific pest e.g. 'spider mites', 'thrips', 'fungus gnats', 'mealybugs'"},
                "severity":       {"type": "string", "enum": ["mild", "moderate", "severe"], "description": "How bad is it"},
                "symptoms":       {"type": "string", "description": "What Stef observed"},
                "treatment_plan": {"type": "string", "description": "Recommended treatment steps"},
                "next_treatment": {"type": "string", "description": "ISO date YYYY-MM-DD for next treatment"},
                "quarantine":     {"type": "boolean", "description": "Should this plant be isolated?"},
                "notes":          {"type": "string", "description": "Any extra notes"},
            },
            "required": ["plant_id", "type"],
        },
    },
    {
        "name": "schedule_reminder",
        "description": "Schedule a Telegram reminder for a plant care task — spider mite re-treatment, repotting check, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message":   {"type": "string", "description": "Reminder text e.g. '🌿 Treat Philodendron for spider mites (round 2)'"},
                "remind_at": {"type": "string", "description": "ISO 8601 datetime in SAST (UTC+2) e.g. '2026-06-27T09:00:00+02:00'"},
            },
            "required": ["message", "remind_at"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List all pending plant care reminders.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending plant reminder by ID (from list_reminders).",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Full UUID of the reminder"},
            },
            "required": ["reminder_id"],
        },
    },
]


class PlantAtlasAgent(DeskAgent):
    workspace = Workspace.PLANT_ATLAS
    system_prompt = _SYSTEM

    def _headers(self) -> dict:
        return {"x-headspace-key": settings.florafolio_headspace_key}

    def _json_headers(self) -> dict:
        return {**self._headers(), "Content-Type": "application/json"}

    def _base(self) -> str:
        return f"{settings.florafolio_url}/api/headspace"

    async def _search_plants(self, query: str = "") -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base()}/plants", headers=self._headers())
            resp.raise_for_status()
            all_plants = resp.json()

        if query:
            q = query.lower()
            all_plants = [
                p for p in all_plants
                if q in (p.get("displayName") or "").lower()
                or q in (p.get("botanicalName") or "").lower()
                or q in (p.get("commonName") or "").lower()
                or q in (p.get("location") or "").lower()
            ]

        if not all_plants:
            return "No plants found matching that query."
        slim = [
            {k: p[k] for k in ("id", "displayName", "botanicalName", "location", "status") if p.get(k)}
            for p in all_plants[:150]
        ]
        return json.dumps(slim)

    async def _get_plant(self, plant_id: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base()}/plants/{plant_id}", headers=self._headers())
            resp.raise_for_status()
            return json.dumps(resp.json())

    async def _add_plant(self, display_name: str, **kwargs) -> str:
        body: dict = {"displayName": display_name}
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
            resp = await client.post(f"{self._base()}/plants", headers=self._json_headers(), json=body)
            resp.raise_for_status()
            plant = resp.json()
        return f"Added '{plant['displayName']}' to FloraFolio (id: {plant['id']})"

    async def _update_plant(self, plant_id: str, **kwargs) -> str:
        field_map = {
            "display_name":    "displayName",
            "botanical_name":  "botanicalName",
            "location":        "location",
            "care_notes":      "careNotes",
            "status":          "status",
            "watering_days":   "wateringDays",
            "humidity":        "humidity",
            "light":           "light",
            "feeding_schedule":"feedingSchedule",
        }
        body = {field_map[k]: v for k, v in kwargs.items() if v is not None and k in field_map}
        if not body:
            return "Nothing to update — no valid fields provided."
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{self._base()}/plants/{plant_id}", headers=self._json_headers(), json=body
            )
            resp.raise_for_status()
            plant = resp.json()
        updated = ", ".join(f"{k}={v}" for k, v in body.items())
        return f"Updated {plant.get('displayName', plant_id)}: {updated}"

    async def _log_care(self, plant_id: str, care_type: str, date: str | None = None, notes: str | None = None) -> str:
        body: dict = {"plantId": plant_id, "type": care_type}
        if date:
            body["date"] = date
        if notes:
            body["notes"] = notes
        async with httpx.AsyncClient(timeout=10) as client:
            # Get plant name for confirmation message
            pr = await client.get(f"{self._base()}/plants/{plant_id}", headers=self._headers())
            plant_name = pr.json().get("displayName", plant_id) if pr.is_success else plant_id

            resp = await client.post(f"{self._base()}/care-logs", headers=self._json_headers(), json=body)
            resp.raise_for_status()
            log = resp.json()
        return f"Logged {care_type} for {plant_name} on {log.get('date', 'today')}." + (f" Notes: {notes}" if notes else "")

    async def _get_care_history(self, plant_id: str, care_type: str | None = None, limit: int = 10) -> str:
        params: dict = {"plantId": plant_id, "limit": str(min(limit, 50))}
        if care_type:
            params["type"] = care_type
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base()}/care-logs", headers=self._headers(), params=params)
            resp.raise_for_status()
            logs = resp.json()
        if not logs:
            msg = f"No {care_type or 'care'} logs found for this plant."
            return msg
        return json.dumps(logs)

    async def _get_issues(self, plant_id: str | None = None, status: str = "active") -> str:
        params: dict = {"status": status}
        if plant_id:
            params["plantId"] = plant_id
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base()}/issues", headers=self._headers(), params=params)
            resp.raise_for_status()
            result = resp.json()
        if not result:
            return f"No {status} issues found."
        return json.dumps(result)

    async def _log_issue(self, plant_id: str, issue_type: str, **kwargs) -> str:
        body: dict = {"plantId": plant_id, "type": issue_type}
        field_map = {
            "pest_type":      "pestType",
            "severity":       "severity",
            "symptoms":       "symptoms",
            "treatment_plan": "treatmentPlan",
            "next_treatment": "nextTreatment",
            "quarantine":     "quarantine",
            "notes":          "notes",
        }
        for k, v in kwargs.items():
            if v is not None and k in field_map:
                body[field_map[k]] = v
        async with httpx.AsyncClient(timeout=10) as client:
            pr = await client.get(f"{self._base()}/plants/{plant_id}", headers=self._headers())
            plant_name = pr.json().get("displayName", plant_id) if pr.is_success else plant_id

            resp = await client.post(f"{self._base()}/issues", headers=self._json_headers(), json=body)
            resp.raise_for_status()
        pest = kwargs.get("pest_type") or issue_type
        return f"Issue logged for {plant_name}: {pest} ({kwargs.get('severity', 'mild')}). Issue ID: {resp.json().get('id', '—')}"

    async def _schedule_reminder(self, session: AsyncSession, message: str, remind_at: str) -> str:
        from services.reminders import create_reminder, set_arq_job_id
        from workers.arq_pool import get_pool
        dt = datetime.fromisoformat(remind_at)
        dt_utc = dt.astimezone(timezone.utc)
        reminder = await create_reminder(session, message, dt_utc, "plant_atlas")
        pool = await get_pool()
        job = await pool.enqueue_job(
            "send_telegram_reminder", message=message,
            reminder_id=str(reminder.id), _defer_until=dt_utc,
        )
        if job:
            await set_arq_job_id(session, reminder, job.job_id)
        else:
            await session.commit()
        local_str = dt.strftime("%-d %b at %-I:%M %p")
        return f"Reminder set for {local_str} SAST: {message}"

    async def _list_reminders(self, session: AsyncSession) -> str:
        from services.reminders import list_pending
        reminders = await list_pending(session, "plant_atlas")
        if not reminders:
            return "No pending plant reminders."
        SAST = timezone(timedelta(hours=2))
        lines = []
        for r in reminders:
            local = r.remind_at.astimezone(SAST).strftime("%-d %b at %-I:%M %p")
            lines.append(f"• [{str(r.id)[:8]}] {local} — {r.message}")
        return "\n".join(lines)

    async def _cancel_reminder(self, session: AsyncSession, reminder_id: str) -> str:
        from services.reminders import cancel
        ok = await cancel(session, reminder_id)
        return "Reminder cancelled." if ok else "Reminder not found — it may have already fired."

    async def _execute_tool(self, name: str, tool_input: dict, session: AsyncSession) -> str:
        try:
            if name == "save_agent_name":
                return await save_agent_name(tool_input["name"], self.workspace.value, session)
            elif name == "search_plants":
                return await self._search_plants(tool_input.get("query", ""))
            elif name == "get_plant":
                return await self._get_plant(tool_input["plant_id"])
            elif name == "add_plant":
                display_name = tool_input.pop("display_name")
                return await self._add_plant(display_name, **tool_input)
            elif name == "update_plant":
                plant_id = tool_input.pop("plant_id")
                return await self._update_plant(plant_id, **tool_input)
            elif name == "log_care":
                return await self._log_care(
                    tool_input["plant_id"],
                    tool_input["type"],
                    date=tool_input.get("date"),
                    notes=tool_input.get("notes"),
                )
            elif name == "get_care_history":
                return await self._get_care_history(
                    tool_input["plant_id"],
                    care_type=tool_input.get("type"),
                    limit=tool_input.get("limit", 10),
                )
            elif name == "get_issues":
                return await self._get_issues(
                    plant_id=tool_input.get("plant_id"),
                    status=tool_input.get("status", "active"),
                )
            elif name == "log_issue":
                plant_id = tool_input.pop("plant_id")
                issue_type = tool_input.pop("type")
                return await self._log_issue(plant_id, issue_type, **tool_input)
            elif name == "schedule_reminder":
                return await self._schedule_reminder(session, tool_input["message"], tool_input["remind_at"])
            elif name == "list_reminders":
                return await self._list_reminders(session)
            elif name == "cancel_reminder":
                return await self._cancel_reminder(session, tool_input["reminder_id"])
            return "Unknown tool"
        except Exception as e:
            return f"Tool error: {e}"

    async def handle(
        self,
        message: str,
        context: dict,
        session: AsyncSession,
        attachments: list | None = None,
    ) -> AsyncIterator[ServerSentEvent]:
        memory_context = "\n".join(f"- {m['content']}" for m in context.get("memories", []))
        current_dt = context.get("current_datetime", "")
        system = f"Current date and time: {current_dt}\n\n" + self.system_prompt if current_dt else self.system_prompt
        if memory_context:
            system += f"\n\nRelevant context from memory:\n{memory_context}"

        messages = [*context.get("history", []), {"role": "user", "content": self._user_content(message, attachments)}]

        try:
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
