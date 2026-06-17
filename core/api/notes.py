from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database import get_session
from models.db import Note, User

router = APIRouter(prefix="/notes", tags=["notes"])

_SINGLETON_ID = "00000000-0000-0000-0000-000000000001"


async def _get_or_create(session: AsyncSession) -> Note:
    result = await session.execute(sa.select(Note).where(Note.id == _SINGLETON_ID))
    note = result.scalar_one_or_none()
    if not note:
        note = Note(id=_SINGLETON_ID, content="")
        session.add(note)
        await session.flush()
    return note


class NoteSave(BaseModel):
    content: str


class NoteAppend(BaseModel):
    text: str


@router.get("/")
async def get_notes(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await _get_or_create(session)
    await session.commit()
    return {"content": note.content, "updated_at": note.updated_at.isoformat()}


@router.put("/")
async def save_notes(
    body: NoteSave,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await _get_or_create(session)
    note.content = body.content
    note.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"content": note.content, "updated_at": note.updated_at.isoformat()}


@router.post("/append")
async def append_note(
    body: NoteAppend,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await _get_or_create(session)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%-d %b %H:%M")
    entry = f"- **{timestamp}** {body.text.strip()}"
    note.content = entry + ("\n" + note.content if note.content else "")
    note.updated_at = now
    await session.commit()
    return {"content": note.content, "updated_at": note.updated_at.isoformat()}
