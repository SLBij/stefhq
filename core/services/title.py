from anthropic import AsyncAnthropic

from config import settings

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)


async def generate_title(user_message: str, assistant_message: str) -> str:
    resp = await _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{
            "role": "user",
            "content": (
                "Write a 3-5 word title for this conversation. "
                "Reply with ONLY the title — no punctuation, no quotes, no explanation.\n\n"
                f"User: {user_message[:500]}\n\nAssistant: {assistant_message[:500]}"
            ),
        }],
    )
    return resp.content[0].text.strip()
