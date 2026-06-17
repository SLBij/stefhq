from abc import ABC, abstractmethod
from typing import AsyncIterator

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from agents.router import Workspace
from config import settings
from services.streaming import ServerSentEvent


class DeskAgent(ABC):
    workspace: Workspace
    system_prompt: str

    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    @staticmethod
    def _user_content(message: str, attachments: list | None) -> str | list:
        if not attachments:
            return message
        blocks: list = []
        for att in attachments:
            if att.get("type") == "image":
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": att.get("media_type", "image/jpeg"),
                        "data": att["data"],
                    },
                })
        if message:
            blocks.append({"type": "text", "text": message})
        return blocks if blocks else message

    @abstractmethod
    async def handle(
        self,
        message: str,
        context: dict,
        session: AsyncSession,
        attachments: list | None = None,
    ) -> AsyncIterator[ServerSentEvent]:
        ...

    def get_tools(self) -> list[dict]:
        return []
