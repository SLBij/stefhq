import asyncio
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
    "https://www.googleapis.com/auth/gmail.readonly",
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


def _extract_body(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    data = payload.get("body", {}).get("data", "")
    if data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    return ""


async def gmail_list_messages(
    access_token: str,
    query: str = "in:inbox",
    max_results: int = 10,
) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_GMAIL_BASE}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": query, "maxResults": min(max_results, 20)},
        )
        if not resp.is_success:
            raise ValueError(f"Gmail list {resp.status_code}: {resp.text}")
        ids = resp.json().get("messages", [])
        if not ids:
            return []

        async def _get_meta(msg_id: str) -> dict:
            r = await client.get(
                f"{_GMAIL_BASE}/messages/{msg_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            r.raise_for_status()
            m = r.json()
            hdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            return {
                "id": msg_id,
                "from": hdrs.get("From", ""),
                "subject": hdrs.get("Subject", "(no subject)"),
                "date": hdrs.get("Date", ""),
                "snippet": m.get("snippet", ""),
            }

        return list(await asyncio.gather(*[_get_meta(m["id"]) for m in ids]))


async def gmail_get_message(access_token: str, message_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_GMAIL_BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
        )
        if not resp.is_success:
            raise ValueError(f"Gmail read {resp.status_code}: {resp.text}")
        data = resp.json()
    payload = data.get("payload", {})
    hdrs = {h["name"]: h["value"] for h in payload.get("headers", [])}
    body = _extract_body(payload) or data.get("snippet", "")
    return {
        "id": message_id,
        "from": hdrs.get("From", ""),
        "to": hdrs.get("To", ""),
        "subject": hdrs.get("Subject", "(no subject)"),
        "date": hdrs.get("Date", ""),
        "body": body[:5000],
    }


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
            "id": item.get("id"),
            "title": item.get("summary", "(no title)"),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "description": item.get("description", ""),
            "location": item.get("location", ""),
        })
    return events


async def calendar_get_event(access_token: str, event_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_CALENDAR_BASE}/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not resp.is_success:
            raise ValueError(f"Calendar get {resp.status_code}: {resp.text}")
        item = resp.json()
    start = item.get("start", {})
    end = item.get("end", {})
    return {
        "id": item["id"],
        "title": item.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "description": item.get("description", ""),
        "location": item.get("location", ""),
    }


async def calendar_update_event(
    access_token: str,
    event_id: str,
    title: str | None = None,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    patch: dict = {}
    if title is not None:
        patch["summary"] = title
    if start_datetime is not None:
        patch["start"] = {"dateTime": start_datetime, "timeZone": "Africa/Johannesburg"}
    if end_datetime is not None:
        patch["end"] = {"dateTime": end_datetime, "timeZone": "Africa/Johannesburg"}
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(
            f"{_CALENDAR_BASE}/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            json=patch,
        )
        if not resp.is_success:
            raise ValueError(f"Calendar update {resp.status_code}: {resp.text}")
        data = resp.json()
    return {"event_id": data["id"], "html_link": data.get("htmlLink", "")}
