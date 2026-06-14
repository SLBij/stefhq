import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database import get_session
from models.db import Task, User

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    priority: str = "medium"
    due_date: str | None = None
    tags: list[str] = []


class TaskUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    title: str | None = None
    description: str | None = None


def _serialize(t: Task) -> dict:
    return {
        "id": str(t.id),
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "due_date": t.due_date.date().isoformat() if t.due_date else None,
        "tags": t.tags,
        "source": t.source,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


@router.get("/")
async def list_tasks(
    status: str = Query("open"),
    priority: str | None = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    query = sa.select(Task)
    if status == "all":
        pass
    elif status == "active":
        query = query.where(Task.status.in_(["open", "in_progress"]))
    else:
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    query = query.order_by(Task.created_at.desc()).limit(100)
    result = await session.execute(query)
    return [_serialize(t) for t in result.scalars().all()]


@router.post("/")
async def create_task(
    body: TaskCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    parsed_due = None
    if body.due_date:
        try:
            parsed_due = datetime.fromisoformat(body.due_date).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    task = Task(
        title=body.title,
        description=body.description,
        priority=body.priority,
        due_date=parsed_due,
        tags=body.tags,
        source="inbox",
    )
    session.add(task)
    await session.commit()
    return _serialize(task)


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    body: TaskUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(sa.select(Task).where(Task.id == uuid.UUID(task_id)))
    task = result.scalar_one()
    if body.status:
        task.status = body.status
    if body.priority:
        task.priority = body.priority
    if body.title:
        task.title = body.title
    if body.description is not None:
        task.description = body.description
    task.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return _serialize(task)
