"""WhatsApp Cloud API webhook — handles inbound client messages and routes them to Pip."""
import json
import logging

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business import BusinessAgent
from agents.router import Workspace
from config import settings
from database import async_session_factory
from models.db import Conversation, GoogleToken, Message
from services.context import assemble_context
from services.datetime_context import format_current_datetime
from services.whatsapp import send_whatsapp_text
from workers.arq_pool import get_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

_agent = BusinessAgent()


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    """Meta calls this GET to verify the webhook URL during setup."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook")
async def receive_message(request: Request):
    """Receive inbound WhatsApp messages from Meta and route them to Pip."""
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    try:
        entry = body["entry"][0]
        change = entry["changes"][0]["value"]

        # Ignore delivery/read status updates
        if "messages" not in change:
            return {"status": "ok"}

        msg = change["messages"][0]
        sender_phone = msg["from"]

        if msg["type"] != "text":
            await send_whatsapp_text(
                sender_phone,
                "Hi! I can only handle text messages at the moment. Please type your question.",
            )
            return {"status": "ok"}

        text = msg["text"]["body"]

    except (KeyError, IndexError):
        return {"status": "ok"}

    async with async_session_factory() as session:
        client_info = await _lookup_client_by_phone(sender_phone)

        # One persistent conversation per sender phone number
        conv_title = f"📱 WhatsApp: {sender_phone}"
        result = await session.execute(
            sa.select(Conversation)
            .where(Conversation.workspace == "business", Conversation.title == conv_title)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            conversation = Conversation(workspace="business", title=conv_title)
            session.add(conversation)
            await session.flush()

        # Prefix the message with client identity so Pip has context from the first token
        if client_info:
            prefixed = f"[WhatsApp from {client_info['name']} | phone: {sender_phone}]\n{text}"
        else:
            prefixed = f"[WhatsApp from unknown number: {sender_phone}]\n{text}"

        session.add(Message(conversation_id=conversation.id, role="user", content=prefixed))
        await session.flush()

        context = await assemble_context(
            session=session,
            message=text,
            workspace=Workspace.BUSINESS,
            conversation=conversation,
            entities=[client_info["name"]] if client_info else [],
        )
        context["current_datetime"] = format_current_datetime()
        context["whatsapp_sender_phone"] = sender_phone

        # Pass user_id so Pip can access Google APIs if needed
        uid_result = await session.execute(sa.select(GoogleToken.user_id).limit(1))
        context["user_id"] = uid_result.scalar_one_or_none()

        full_response = ""
        async for event in _agent.handle(prefixed, context, session):
            if event.event == "token":
                full_response += json.loads(event.data).get("content", "")

        session.add(Message(conversation_id=conversation.id, role="assistant", content=full_response))
        await session.commit()

        await send_whatsapp_text(sender_phone, full_response)

        pool = await get_pool()
        await pool.enqueue_job(
            "extract_memories",
            user_message=text,
            assistant_message=full_response,
            workspace="business",
        )

    return {"status": "ok"}


async def process_stef_instruction(client_phone: str, client_name: str, instruction: str) -> bool:
    """Route Stef's Telegram reply through Pip so it's crafted into a proper WhatsApp message."""
    async with async_session_factory() as session:
        conv_title = f"📱 WhatsApp: {client_phone}"
        result = await session.execute(
            sa.select(Conversation)
            .where(Conversation.workspace == "business", Conversation.title == conv_title)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            conversation = Conversation(workspace="business", title=conv_title)
            session.add(conversation)
            await session.flush()

        display = client_name if client_name and client_name.lower() != "unknown" else client_phone
        prefixed = (
            f"[Stef instruction | WhatsApp phone: {client_phone} | client: {display}]\n"
            f"Stef says: {instruction}\n"
            f"Craft a natural WhatsApp reply based on Stef's instruction and send it to {client_phone} "
            f"using send_whatsapp_message. Do not ask for more details. Do not escalate."
        )
        session.add(Message(conversation_id=conversation.id, role="user", content=prefixed))
        await session.flush()

        context = await assemble_context(
            session=session,
            message=instruction,
            workspace=Workspace.BUSINESS,
            conversation=conversation,
            entities=[client_name],
        )
        context["current_datetime"] = format_current_datetime()
        context["whatsapp_sender_phone"] = client_phone

        uid_result = await session.execute(sa.select(GoogleToken.user_id).limit(1))
        context["user_id"] = uid_result.scalar_one_or_none()

        full_response = ""
        async for event in _agent.handle(prefixed, context, session):
            if event.event == "token":
                full_response += json.loads(event.data).get("content", "")

        session.add(Message(conversation_id=conversation.id, role="assistant", content=full_response))
        await session.commit()

    return True


async def _lookup_client_by_phone(phone: str) -> dict | None:
    """Find a CRM client by phone number — tries exact match then last-9-digits suffix."""
    headers = {
        "Authorization": f"Bearer {settings.curtains_supabase_key}",
        "apikey": settings.curtains_supabase_key,
    }
    base = f"{settings.curtains_supabase_url}/rest/v1"

    async with httpx.AsyncClient(timeout=10) as client:
        # Exact match (handles already-normalised numbers)
        resp = await client.get(
            f"{base}/clients",
            headers=headers,
            params={"phone": f"eq.{phone}", "select": "id,name,phone", "limit": "1"},
        )
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]

        # Suffix match — strip non-digits, use last 9 to bridge +27 vs 0 formats
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= 9:
            suffix = digits[-9:]
            resp = await client.get(
                f"{base}/clients",
                headers=headers,
                params={"phone": f"like.*{suffix}", "select": "id,name,phone", "limit": "1"},
            )
            if resp.status_code == 200 and resp.json():
                return resp.json()[0]

    return None
