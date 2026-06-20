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
management desk for her FloraFolio collection, and her co-author for the FloraFolio species encyclopedia.

You have two modes of work:

── PERSONAL COLLECTION ──────────────────────────────────────────────
Tools for Stef's own plants (collection, care, pests):
- search_plants: search or browse the collection — use first when a plant is mentioned
- get_plant: full details for a plant by ID (lastWatered, wateringDays, status, location, etc.)
- add_plant: add a new plant — always search first to check for duplicates
- update_plant: update location, care notes, status, watering schedule, etc.
- log_care: log a care event — watered, fed, inspected, treated, repotted
- get_care_history: recent care logs per plant (when last watered, fed, etc.)
- get_issues / log_issue: pest and disease tracking
- schedule_reminder / list_reminders / cancel_reminder: Telegram care reminders

Care context:
- Cape Town, South Africa — Mediterranean: warm dry summers, mild wet winters
- Most aroids need less water in winter, more in summer
- Common issues: spider mites in dry summer air, fungus gnats from winter overwatering
- Hybrid notation: "A × B" is ONE plant (a cross between A and B) — never split it into two separate entries
- Always use get_care_history to answer "when did I last water X?" — never guess
- When a pest is mentioned: log_issue first, then treatment plan, then offer a reminder

── SPECIES ENCYCLOPEDIA ─────────────────────────────────────────────
Tools for drafting and submitting plant pages to the shared FloraFolio database \
(appears on both the FloraFolio website and app encyclopedia):
- search_species: check if a species already exists in the encyclopedia
- get_species: get the full record for an existing species by ID
- create_species: submit a new species/cultivar entry (all entries start as draft + ai_generated=true)
- update_species: correct or fill in gaps on an existing entry

Encyclopedia workflow:
1. When Stef asks to add a species, first call search_species to check it doesn't exist
2. Draft ALL fields from your botanical knowledge — be thorough and accurate
3. Show the full draft clearly before submitting (Stef reviews it)
4. After confirmation, call create_species
5. Stef adds illustrations manually via the FloraFolio app or admin

Field guidance:
- commonNames: pipe-separated e.g. "Monstera|Swiss Cheese Plant|Split-leaf Philodendron"
- sources: pipe-separated citations/URLs
- commonProblems / funFacts: newline-separated, one item per line
- plantCategory: species | subspecies | variety | form | cultivar | hybrid | trade_name
- confidenceLevel: always use "draft" unless Stef says otherwise
- For cultivars: set parentId to the parent species ID (search_species first to get it)
- hybridParents: pipe-separated botanical names of parent species
- light: low | indirect | bright-indirect | direct
- growthHabit: crawler | climber | upright | trailing | rosette | clumping
- difficulty: easy | moderate | challenging

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
        "name": "delete_plant",
        "description": "Permanently delete a plant record — use only for genuine data entry errors (duplicates, wrong splits, etc.). Always confirm with Stef before deleting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "string", "description": "Plant UUID from search_plants"},
                "reason":   {"type": "string", "description": "Brief reason e.g. 'data entry error — hybrid split incorrectly'"},
            },
            "required": ["plant_id", "reason"],
        },
    },
    {
        "name": "search_species",
        "description": "Search the FloraFolio species encyclopedia by botanical name, common name, or genus. Use before create_species to check for duplicates, or to find parentId for a cultivar entry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Botanical name, common name, or genus to search for"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_species",
        "description": "Get the full species record by ID (from search_species). Use to inspect an existing entry before updating.",
        "input_schema": {
            "type": "object",
            "properties": {
                "species_id": {"type": "string", "description": "Species UUID from search_species"},
            },
            "required": ["species_id"],
        },
    },
    {
        "name": "create_species",
        "description": (
            "Submit a new species or cultivar entry to the FloraFolio encyclopedia. "
            "ALWAYS show the full draft to Stef and wait for confirmation before calling. "
            "All entries are marked aiGenerated=true and confidenceLevel='draft' automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "botanical":          {"type": "string", "description": "Full scientific name e.g. 'Monstera deliciosa'"},
                "common_names":       {"type": "string", "description": "Pipe-separated common names e.g. 'Monstera|Swiss Cheese Plant'"},
                "genus":              {"type": "string", "description": "Genus name e.g. 'Monstera'"},
                "family":             {"type": "string", "description": "Plant family e.g. 'Araceae'"},
                "plant_category":     {"type": "string", "description": "species | subspecies | variety | form | cultivar | hybrid | trade_name"},
                "origin":             {"type": "string", "description": "Geographic origin e.g. 'Central and South America'"},
                "natural_range":      {"type": "string", "description": "More specific habitat / natural range description"},
                "pronunciation":      {"type": "string", "description": "Phonetic pronunciation e.g. 'mon-STAIR-uh deh-lee-see-OH-suh'"},
                "etymology":          {"type": "string", "description": "Meaning/origin of the scientific name"},
                "discovery_year":     {"type": "integer", "description": "Year first described"},
                "first_described_by": {"type": "string", "description": "Botanist who first described it"},
                "light":              {"type": "string", "description": "low | indirect | bright-indirect | direct"},
                "watering_days":      {"type": "integer", "description": "Typical days between waterings"},
                "humidity":           {"type": "string", "description": "low | medium | high"},
                "temp_min":           {"type": "integer", "description": "Minimum temperature (°C)"},
                "temp_max":           {"type": "integer", "description": "Maximum temperature (°C)"},
                "feeding_schedule":   {"type": "string", "description": "e.g. 'Monthly during growing season (spring/summer)'"},
                "growth_rate":        {"type": "string", "description": "slow | medium | fast"},
                "growth_habit":       {"type": "string", "description": "crawler | climber | upright | trailing | rosette | clumping"},
                "mature_height":      {"type": "string", "description": "e.g. '1–3m indoors'"},
                "mature_spread":      {"type": "string", "description": "e.g. '60–90cm'"},
                "difficulty":         {"type": "string", "description": "easy | moderate | challenging"},
                "toxic_to_pets":      {"type": "boolean"},
                "toxic_to_humans":    {"type": "boolean"},
                "toxicity_notes":     {"type": "string", "description": "What's toxic, symptoms, severity"},
                "leaf_description":   {"type": "string", "description": "Detailed leaf morphology"},
                "growth_description": {"type": "string", "description": "Overall growth form and habits"},
                "botanical_notes":    {"type": "string", "description": "Discovery history, taxonomy, interesting botanical context"},
                "care_notes":         {"type": "string", "description": "General care summary"},
                "common_problems":    {"type": "string", "description": "Newline-separated list of common issues"},
                "repotting_notes":    {"type": "string", "description": "When and how to repot"},
                "fun_facts":          {"type": "string", "description": "Newline-separated interesting facts"},
                "parent_id":          {"type": "string", "description": "Species UUID of parent — required for cultivars/varieties (from search_species)"},
                "cultivar_name":      {"type": "string", "description": "Cultivar name without quotes e.g. 'Thai Constellation'"},
                "hybrid_parents":     {"type": "string", "description": "Pipe-separated parent botanical names for hybrids"},
                "sources":            {"type": "string", "description": "Pipe-separated citations or URLs"},
                "confidence_level":   {"type": "string", "description": "draft (default) | community | verified | expert"},
            },
            "required": ["botanical"],
        },
    },
    {
        "name": "update_species",
        "description": "Update fields on an existing species entry. Use species_id from search_species or get_species.",
        "input_schema": {
            "type": "object",
            "properties": {
                "species_id":         {"type": "string", "description": "Species UUID"},
                "botanical":          {"type": "string"},
                "common_names":       {"type": "string"},
                "genus":              {"type": "string"},
                "family":             {"type": "string"},
                "plant_category":     {"type": "string"},
                "origin":             {"type": "string"},
                "natural_range":      {"type": "string"},
                "pronunciation":      {"type": "string"},
                "etymology":          {"type": "string"},
                "discovery_year":     {"type": "integer"},
                "first_described_by": {"type": "string"},
                "light":              {"type": "string"},
                "watering_days":      {"type": "integer"},
                "humidity":           {"type": "string"},
                "temp_min":           {"type": "integer"},
                "temp_max":           {"type": "integer"},
                "feeding_schedule":   {"type": "string"},
                "growth_rate":        {"type": "string"},
                "growth_habit":       {"type": "string"},
                "mature_height":      {"type": "string"},
                "mature_spread":      {"type": "string"},
                "difficulty":         {"type": "string"},
                "toxic_to_pets":      {"type": "boolean"},
                "toxic_to_humans":    {"type": "boolean"},
                "toxicity_notes":     {"type": "string"},
                "leaf_description":   {"type": "string"},
                "growth_description": {"type": "string"},
                "botanical_notes":    {"type": "string"},
                "care_notes":         {"type": "string"},
                "common_problems":    {"type": "string"},
                "repotting_notes":    {"type": "string"},
                "fun_facts":          {"type": "string"},
                "parent_id":          {"type": "string"},
                "cultivar_name":      {"type": "string"},
                "hybrid_parents":     {"type": "string"},
                "sources":            {"type": "string"},
                "confidence_level":   {"type": "string"},
            },
            "required": ["species_id"],
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
            # Token-level OR: each word in the query is checked independently so
            # "Anthurium Warocqueanum × Papillilaminum" still finds "Anthurium woroqueanum x papi"
            tokens = [t for t in q.split() if len(t) > 1 and t not in {"×", "x"}]

            def _matches(p: dict) -> bool:
                haystack = " ".join(filter(None, [
                    p.get("displayName"), p.get("botanicalName"),
                    p.get("commonName"), p.get("location"),
                ])).lower()
                return q in haystack or any(t in haystack for t in tokens)

            all_plants = [p for p in all_plants if _matches(p)]

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

    async def _delete_plant(self, plant_id: str, reason: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(f"{self._base()}/plants/{plant_id}", headers=self._headers())
            if r.status_code == 404:
                return "Plant not found."
            r.raise_for_status()
        return f"Plant deleted ({reason})."

    # ── Species encyclopedia ──────────────────────────────────────────────────

    @staticmethod
    def _snake_to_camel(key: str) -> str:
        parts = key.split("_")
        return parts[0] + "".join(p.capitalize() for p in parts[1:])

    def _species_payload(self, tool_input: dict) -> dict:
        skip = {"species_id"}
        return {self._snake_to_camel(k): v for k, v in tool_input.items() if k not in skip}

    async def _search_species(self, query: str) -> str:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self._base()}/species",
                params={"query": query},
                headers=self._headers(),
            )
            r.raise_for_status()
        rows = r.json()
        if not rows:
            return "No matching species found in the encyclopedia."
        lines = []
        for s in rows:
            names = f" ({s['commonNames']})" if s.get("commonNames") else ""
            lines.append(f"- [{s['id']}] {s['botanical']}{names} — {s.get('confidenceLevel', 'draft')}")
        return "\n".join(lines)

    async def _get_species(self, species_id: str) -> str:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self._base()}/species/{species_id}",
                headers=self._headers(),
            )
            r.raise_for_status()
        return json.dumps(r.json(), indent=2)

    async def _create_species(self, tool_input: dict) -> str:
        payload = self._species_payload(tool_input)
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._base()}/species",
                json=payload,
                headers=self._json_headers(),
            )
            r.raise_for_status()
        row = r.json()
        return f"Species created: [{row['id']}] {row['botanical']} — slug: {row['slug']}"

    async def _update_species(self, species_id: str, tool_input: dict) -> str:
        payload = self._species_payload(tool_input)
        if not payload:
            return "No fields to update."
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{self._base()}/species/{species_id}",
                json=payload,
                headers=self._json_headers(),
            )
            r.raise_for_status()
        row = r.json()
        return f"Species updated: {row['botanical']}"

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
            elif name == "delete_plant":
                return await self._delete_plant(tool_input["plant_id"], tool_input["reason"])
            elif name == "search_species":
                return await self._search_species(tool_input.get("query", ""))
            elif name == "get_species":
                return await self._get_species(tool_input["species_id"])
            elif name == "create_species":
                return await self._create_species(tool_input)
            elif name == "update_species":
                species_id = tool_input.pop("species_id")
                return await self._update_species(species_id, tool_input)
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
