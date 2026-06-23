"""WhatsApp Cloud API helpers."""
import httpx

from config import settings


async def send_whatsapp_text(to_phone: str, message: str) -> bool:
    """Send a plain-text message via the Meta WhatsApp Cloud API."""
    if not settings.whatsapp_phone_number_id or not settings.whatsapp_access_token:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://graph.facebook.com/v20.0/{settings.whatsapp_phone_number_id}/messages",
            headers={
                "Authorization": f"Bearer {settings.whatsapp_access_token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "text",
                "text": {"body": message},
            },
        )
        return resp.status_code == 200


async def notify_stef_escalation(
    client_name: str, client_phone: str, their_message: str, context: str
) -> bool:
    """Forward an unhandled client WhatsApp message to Stef's Telegram via the Pip bot."""
    if not settings.pip_bot_token or not settings.telegram_chat_id:
        return False
    text = (
        f"📱 *WhatsApp escalation*\n"
        f"*From:* {client_name} (`{client_phone}`)\n\n"
        f"*They said:* {their_message}\n\n"
        f"*Context:* {context}\n\n"
        f"Reply here and I'll send it to them on WhatsApp."
    )
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{settings.pip_bot_token}/sendMessage",
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
        )
        return resp.status_code == 200
