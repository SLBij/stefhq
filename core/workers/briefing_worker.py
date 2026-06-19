"""Morning briefing — runs daily at 8:15am SAST (6:15am UTC)."""
from datetime import date, datetime, timedelta, timezone

import httpx
import sqlalchemy as sa
from anthropic import AsyncAnthropic

from config import settings
from database import async_session_factory
from models.db import Task


async def send_telegram_reminder(ctx, message: str, reminder_id: str | None = None):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    async with httpx.AsyncClient(timeout=10) as http:
        await http.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": settings.telegram_chat_id,
                "text": f"⏰ *Reminder*\n\n{message}",
                "parse_mode": "Markdown",
            },
        )
    if reminder_id:
        from services.reminders import mark_fired
        async with async_session_factory() as session:
            await mark_fired(session, reminder_id)


async def send_morning_briefing(ctx):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    today = date.today()

    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(Task)
            .where(Task.status.in_(["open", "in_progress"]))
            .order_by(Task.priority.desc(), Task.due_date.asc().nullslast())
            .limit(30)
        )
        tasks = list(result.scalars().all())

    if not tasks:
        return

    task_lines = []
    for t in tasks:
        if t.due_date and t.due_date.date() == today:
            due = " *(due today)*"
        elif t.due_date:
            due = f" (due {t.due_date.strftime('%d %b')})"
        else:
            due = ""
        status = "🔄" if t.status == "in_progress" else "○"
        task_lines.append(f"{status} [{t.priority}] {t.title}{due}")

    task_block = "\n".join(task_lines)
    today_str = today.strftime("%A, %d %B %Y")

    prompt = f"""Today is {today_str}.

Open tasks:
{task_block}

Write Stef a brief, friendly morning briefing for Telegram. Warm but concise — 1-2 sentences intro, \
then the task list (keep it as-is, just tidy formatting). Flag anything due today. \
Use Telegram Markdown (*bold*, _italic_). Keep it under 200 words."""

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text

    async with httpx.AsyncClient(timeout=10) as http:
        await http.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
        )


async def send_evening_briefing(ctx):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    today = date.today()

    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(Task)
            .where(Task.status.in_(["open", "in_progress", "done"]))
            .where(Task.updated_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
            .order_by(Task.status.asc(), Task.priority.desc())
            .limit(30)
        )
        tasks = list(result.scalars().all())

    if not tasks:
        return

    done = [t for t in tasks if t.status == "done"]
    open_ = [t for t in tasks if t.status in ("open", "in_progress")]

    done_lines = "\n".join(f"✅ {t.title}" for t in done) if done else "_Nothing completed today_"
    open_lines = "\n".join(f"○ [{t.priority}] {t.title}" for t in open_) if open_ else "_All clear_"
    today_str = today.strftime("%A, %d %B")

    prompt = f"""Today is {today_str}. End of day wrap-up for Stef.

Done today:
{done_lines}

Still open:
{open_lines}

Write a short, warm end-of-day check-in for Telegram. Acknowledge what got done, note what's still on the list without being naggy, \
and close with something encouraging. Use Telegram Markdown (*bold*, _italic_). Under 150 words."""

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text

    async with httpx.AsyncClient(timeout=10) as http:
        await http.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
        )
