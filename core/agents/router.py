import json
from dataclasses import dataclass
from enum import Enum

from anthropic import AsyncAnthropic

from config import settings


class Workspace(str, Enum):
    GLOBAL = "global"
    BUSINESS = "business"
    PLANT_ATLAS = "plant_atlas"
    ROUND_TABLE = "round_table"
    HIVE_MIND = "hive_mind"
    INBOX = "inbox"


class IntentType(str, Enum):
    QUESTION = "question"
    ACTION = "action"
    RESEARCH = "research"
    CROSS_WORKSPACE = "cross_workspace"


@dataclass
class RoutingDecision:
    workspace: Workspace
    intent_type: IntentType
    entities: list[str]
    reasoning: str


_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

_SYSTEM = """You are a routing agent for Stef's personal AI system (Stef HQ).
Classify the incoming message and decide which workspace should handle it.

Workspaces:
- business: CRM, clients, invoices, jobs, curtains business (Certain Curtains)
- plant_atlas: plants, FloraFolio, plant care, growing, plant photos
- round_table: coding, project planning, technical work, software
- hive_mind: personal chat, thinking out loud, general questions, journaling, system questions, memory, anything that doesn't fit another workspace
- inbox: tasks, reminders, to-dos, action items, things to do or remember

IMPORTANT: If [Current workspace] is provided and the message fits reasonably within that workspace, stay there — only reroute when the message clearly belongs to a different workspace (e.g. CRM question in hive_mind → business).

Respond with JSON only, no prose:
{"workspace": "<workspace>", "intent_type": "question", "entities": ["named", "entities"], "reasoning": "one sentence"}

intent_type MUST be exactly one of: question, action, research, cross_workspace"""


async def route(
    message: str,
    current_workspace: Workspace | None = None,
    attachments: list | None = None,
) -> RoutingDecision:
    content = message
    if attachments:
        content += f"\n[Attachments: {len(attachments)} file(s)]"
    if current_workspace:
        content += f"\n[Current workspace: {current_workspace}]"

    response = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    data = json.loads(raw)
    try:
        intent = IntentType(data["intent_type"])
    except ValueError:
        intent = IntentType.QUESTION

    routed_ws = Workspace(data["workspace"])
    # global is a memory category, not a chat workspace — fall back to current or hive_mind
    if routed_ws == Workspace.GLOBAL:
        routed_ws = current_workspace or Workspace.HIVE_MIND

    return RoutingDecision(
        workspace=routed_ws,
        intent_type=intent,
        entities=data.get("entities", []),
        reasoning=data["reasoning"],
    )
