"""Shared naming tool for all workspace agents."""
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import Memory
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


def agent_name_prompt(role_hint: str) -> str:
    """Return a naming prompt tailored to a specific agent role."""
    return f"""
## Your name
You have a personal name stored in memory. If you see it in the memory context above, use it — \
introduce yourself with it and sign off with it. \
If no name appears, choose one now that fits a {role_hint} — something short and distinctive, \
save it with save_agent_name (just the name itself, e.g. 'Nova' not 'My name is Nova'), \
and introduce yourself to Stef."""


async def save_agent_name(name: str, workspace: str, session: AsyncSession) -> str:
    # Only one name should ever exist per workspace — clear stale/duplicate entries first
    await session.execute(
        sa.delete(Memory).where(Memory.workspace == workspace, Memory.tags.contains(["agent_name"]))
    )
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
