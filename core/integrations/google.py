import base64
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.db import GoogleToken

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar",
]

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


async def _do_refresh(refresh_token: str) -> tuple[str, datetime]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        })
        resp.raise_for_status()
        data = resp.json()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"] - 60)
    return data["access_token"], expires_at


async def get_access_token(user_id: uuid.UUID, session: AsyncSession) -> str:
    result = await session.execute(
        sa.select(GoogleToken).where(GoogleToken.user_id == user_id)
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise ValueError(
            "Google account not connected. Visit https://stefhq.io/google-connect to link your account."
        )
    if datetime.now(timezone.utc) >= token.expires_at:
        new_access, new_expires = await _do_refresh(token.refresh_token)
        token.access_token = new_access
        token.expires_at = new_expires
        token.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return token.access_token


def _build_raw_email(to_email: str, to_name: str | None, subject: str, body: str,
                     cc: str | None = None) -> str:
    msg = EmailMessage()
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


async def gmail_create_draft(
    access_token: str,
    to_email: str,
    subject: str,
    body: str,
    to_name: str | None = None,
    cc: str | None = None,
) -> dict:
    raw = _build_raw_email(to_email, to_name, subject, body, cc)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_GMAIL_BASE}/drafts",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"message": {"raw": raw}},
        )
        if not resp.is_success:
            raise ValueError(f"Gmail {resp.status_code}: {resp.text}")
        data = resp.json()
    draft_id = data.get("id")
    if not draft_id:
        raise ValueError(f"Gmail draft created but no id in response: {data}")
    return {"draft_id": draft_id}


async def gmail_send_draft(access_token: str, draft_id: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_GMAIL_BASE}/drafts/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"id": draft_id},
        )
        if not resp.is_success:
            raise ValueError(f"Gmail send {resp.status_code}: {resp.text}")


async def calendar_create_event(
    access_token: str,
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    event: dict = {
        "summary": title,
        "start": {"dateTime": start_datetime, "timeZone": "Africa/Johannesburg"},
        "end": {"dateTime": end_datetime, "timeZone": "Africa/Johannesburg"},
    }
    if description:
        event["description"] = description
    if location:
        event["location"] = location
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            _CALENDAR_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            json=event,
        )
        resp.raise_for_status()
        data = resp.json()
    return {"event_id": data["id"], "html_link": data.get("htmlLink", "")}


async def calendar_list_events(access_token: str, days_ahead: int = 7) -> list[dict]:
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=min(days_ahead, 30))
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            _CALENDAR_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "timeMin": now.isoformat(),
                "timeMax": time_max.isoformat(),
                "orderBy": "startTime",
                "singleEvents": "true",
                "maxResults": "20",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    events = []
    for item in data.get("items", []):
        start = item.get("start", {})
        end = item.get("end", {})
        events.append({
            "title": item.get("summary", "(no title)"),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "description": item.get("description", ""),
            "location": item.get("location", ""),
        })
    return events
