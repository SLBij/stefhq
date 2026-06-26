from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database import get_session
from models.db import Note, User

router = APIRouter(prefix="/notes", tags=["notes"])


async def _get_or_create(session: AsyncSession, title: str = "Notes") -> Note:
    result = await session.execute(sa.select(Note).where(Note.title == title))
    note = result.scalar_one_or_none()
    if not note:
        note = Note(title=title, content="")
        session.add(note)
        await session.flush()
    return note


class NoteSave(BaseModel):
    content: str


class NoteAppend(BaseModel):
    text: str


@router.get("/")
async def list_notes(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(sa.select(Note).order_by(Note.updated_at.desc()))
    notes = result.scalars().all()
    return [{"title": n.title, "updated_at": n.updated_at.isoformat()} for n in notes]


@router.get("/{title}")
async def get_note(
    title: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await _get_or_create(session, title)
    await session.commit()
    return {"title": note.title, "content": note.content, "updated_at": note.updated_at.isoformat()}


@router.put("/{title}")
async def save_note(
    title: str,
    body: NoteSave,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await _get_or_create(session, title)
    note.content = body.content
    note.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"title": note.title, "content": note.content, "updated_at": note.updated_at.isoformat()}


@router.delete("/{title}")
async def delete_note(
    title: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(sa.select(Note).where(Note.title == title))
    note = result.scalar_one_or_none()
    if note:
        await session.delete(note)
        await session.commit()
    return {"deleted": title}


@router.post("/{title}/append")
async def append_note(
    title: str,
    body: NoteAppend,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await _get_or_create(session, title)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%-d %b %H:%M")
    entry = f"- **{timestamp}** {body.text.strip()}"
    note.content = entry + ("\n" + note.content if note.content else "")
    note.updated_at = now
    await session.commit()
    return {"title": note.title, "content": note.content, "updated_at": note.updated_at.isoformat()}
