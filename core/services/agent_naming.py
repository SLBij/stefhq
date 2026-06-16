"""Shared naming tool for all workspace agents."""
from sqlalchemy.ext.asyncio import AsyncSession

from services.memory import save_memory

AGENT_NAME_TOOL = {
    "name": "save_agent_name",
    "description": "Save your chosen name to memory. Call this once on your first conversation when no name is in context.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Your chosen name — just the name itself, e.g. 'Nova' not 'My name is Nova'.",
            }
        },
        "required": ["name"],
    },
}

AGENT_NAME_PROMPT = """
## Your name
You have a personal name stored in memory. If you see it in the memory context above, use it — \
introduce yourself with it and sign off with it. If no name appears, choose one now \
(something that fits your personality and workspace), save it with save_agent_name, \
and introduce yourself to Stef."""


async def save_agent_name(name: str, workspace: str, session: AsyncSession) -> str:
    await save_memory(
        session=session,
        content=name,
        workspace=workspace,
        memory_type="fact",
        confidence=1.0,
        auto_extracted=False,
        tags=["agent_name"],
    )
    return "Name saved."
