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

    @abstractmethod
    async def handle(
        self,
        message: str,
        context: dict,
        session: AsyncSession,
    ) -> AsyncIterator[ServerSentEvent]:
        ...

    def get_tools(self) -> list[dict]:
        return []
