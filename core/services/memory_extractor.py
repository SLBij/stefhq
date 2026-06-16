import json
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

from config import settings

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

_SYSTEM = """You are a memory extraction agent for Stef's personal AI system (Stef HQ).
Extract memorable facts, preferences, events, and knowledge from the conversation exchange below.

Only extract things that would be useful to remember in FUTURE conversations. Do NOT extract:
- Generic conversational filler ("thanks", "sounds good", "ok", "testing")
- Obvious or general knowledge
- Temporary states ("I'm busy right now")
- Things said in a test or demo context
- Tool failures, errors, or anything the agent couldn't do — these are events, not facts. Never save capability limitations or "agent can't do X" observations.

Workspaces:
- global: applies across all contexts (personal preferences, general facts about Stef)
- business: CRM, clients, jobs, invoices, Certain Curtains
- plant_atlas: plants, FloraFolio, gardening, plant care
- round_table: coding, projects, software, technical decisions
- hive_mind: personal reflections, thinking, journaling, general chat
- inbox: email, communication, drafting

Types: fact | preference | event | document

Assign confidence 0.0–1.0 based on how clearly and definitively the information was stated.

Respond with JSON only — no prose, no markdown fences:
{"memories": [{"content": "concise self-contained statement", "workspace": "...", "type": "...", "confidence": 0.85, "tags": ["tag1"]}]}

If nothing is worth remembering, respond with exactly: {"memories": []}"""


@dataclass
class CandidateMemory:
    content: str
    workspace: str
    memory_type: str
    confidence: float
    tags: list[str] = field(default_factory=list)


async def extract_from_exchange(
    user_message: str,
    assistant_message: str,
    workspace: str,
) -> list[CandidateMemory]:
    exchange = f"[Current workspace: {workspace}]\n\nUser: {user_message}\n\nAssistant: {assistant_message}"

    response = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": exchange}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    data = json.loads(raw)
    return [
        CandidateMemory(
            content=m["content"],
            workspace=m.get("workspace", workspace),
            memory_type=m.get("type", "fact"),
            confidence=float(m.get("confidence", 0.5)),
            tags=m.get("tags", []),
        )
        for m in data.get("memories", [])
    ]
