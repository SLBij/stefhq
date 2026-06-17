from ollama import AsyncClient

from config import settings

_client = AsyncClient(host=settings.ollama_base_url)


async def embed(text: str) -> list[float]:
    try:
        response = await _client.embeddings(model=settings.embedding_model, prompt=text)
        return response.embedding or []
    except Exception:
        return []


async def embed_batch(texts: list[str]) -> list[list[float]]:
    return [await embed(t) for t in texts]
