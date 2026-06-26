"""Pip bot Telegram relay — polls for Stef's replies to escalation messages and routes them through Pip."""
import asyncio
import json
import logging

import httpx
from redis.asyncio import from_url as redis_from_url

from config import settings

logger = logging.getLogger(__name__)

_POLL_TIMEOUT = 30  # seconds for long-polling


async def run_pip_relay() -> None:
    """Long-poll the Pip Telegram bot and relay Stef's replies to WhatsApp clients via Pip."""
    bot_token = settings.pip_bot_token
    if not bot_token:
        logger.info("PIP_BOT_TOKEN not set — relay loop not started")
        return

    redis = await redis_from_url(settings.redis_url)
    offset = 0

    logger.info("Pip relay loop started")
    while True:
        try:
            updates = await _get_updates(bot_token, offset)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Pip relay getUpdates error: %s", e)
            await asyncio.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                await _handle_update(update, redis, bot_token)
            except Exception as e:
                logger.warning("Pip relay handle_update error: %s", e)

    await redis.aclose()


async def _get_updates(bot_token: str, offset: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=_POLL_TIMEOUT + 5) as client:
        resp = await client.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"offset": offset, "timeout": _POLL_TIMEOUT, "allowed_updates": ["message"]},
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("result", [])


async def _handle_update(update: dict, redis, bot_token: str) -> None:
    message = update.get("message")
    if not message:
        return

    reply_to = message.get("reply_to_message")
    if not reply_to:
        return

    text = message.get("text", "").strip()
    if not text:
        return

    reply_to_id = reply_to["message_id"]
    raw = await redis.get(f"pip:escalation:{reply_to_id}")
    if not raw:
        return

    data = json.loads(raw.decode())
    client_phone = data["phone"]
    client_name = data.get("name", "the client")

    # Route through Pip so it crafts a proper client-facing WhatsApp reply
    from api.whatsapp import process_stef_instruction
    await process_stef_instruction(client_phone, client_name, text)

    await _send_telegram_message(bot_token, settings.telegram_chat_id, f"✅ Pip is replying to {client_name} on WhatsApp.")


async def _send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
