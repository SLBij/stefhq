import json
import uuid
from datetime import date

from workers.arq_pool import get_pool

_KEY_PREFIX = "volt_notes:"


def _key(project: str) -> str:
    return f"{_KEY_PREFIX}{project.lower().strip()}"


async def add_note(project: str, text: str, kind: str = "todo") -> dict:
    """kind: todo | bug | progress | question"""
    pool = await get_pool()
    notes = await _load(pool, project)
    note = {
        "id": uuid.uuid4().hex[:8],
        "date": date.today().isoformat(),
        "kind": kind,
        "text": text,
        "status": "open",
    }
    notes.append(note)
    await pool.set(_key(project), json.dumps(notes))
    return note


async def list_notes(project: str, status: str | None = "open") -> list[dict]:
    pool = await get_pool()
    notes = await _load(pool, project)
    if status:
        notes = [n for n in notes if n["status"] == status]
    return notes


async def resolve_note(project: str, note_id: str) -> bool:
    pool = await get_pool()
    notes = await _load(pool, project)
    found = False
    for n in notes:
        if n["id"] == note_id:
            n["status"] = "done"
            found = True
            break
    if found:
        await pool.set(_key(project), json.dumps(notes))
    return found


async def _load(pool, project: str) -> list[dict]:
    val = await pool.get(_key(project))
    return json.loads(val) if val else []
