"""Embedding providers, chosen by configuration.

* ``gemini``            -- Gemini embedContent API (external).
* ``openai_compatible`` -- any /v1/embeddings server: text-embeddings-inference,
  Ollama, LM Studio, vLLM... e.g. EmbeddingGemma running locally.
* ``disabled``          -- semantic search is off; keyword search still works.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import Forbidden, ProviderUnavailable
from app.modules.pikirler.integrations.http import ProviderError, bearer, with_retries


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int
    is_external: bool

    async def embed_text(self, text: str) -> list[float]:
        """Embed a stored document (a thought)."""

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query (some models use a different instruction)."""


def _check(vector: object, dimensions: int, provider: str) -> list[float]:
    if not isinstance(vector, list) or not vector:
        raise ProviderError(f"{provider} returned no embedding")
    if len(vector) != dimensions:
        raise ProviderError(
            f"{provider} returned {len(vector)} dimensions, configured {dimensions}"
        )
    return [float(x) for x in vector]


class GeminiEmbeddingProvider:
    name = "gemini"
    is_external = True

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key:
            raise ProviderUnavailable("PIKIRLER_GEMINI_API_KEY is not set")
        self.model = settings.embedding_model or "gemini-embedding-001"
        self.dimensions = settings.embedding_dimensions
        self._settings = settings

    async def _embed(self, text: str, task: str) -> list[float]:
        s = self._settings
        url = f"{s.gemini_base_url}/models/{self.model}:embedContent"
        body = {
            "content": {"parts": [{"text": text[: s.embedding_max_chars]}]},
            "taskType": task,
            "outputDimensionality": self.dimensions,
        }

        async def call() -> list[float]:
            async with httpx.AsyncClient(timeout=s.embedding_timeout_seconds) as client:
                # Key in a header: query-string keys end up in proxy/access logs.
                resp = await client.post(
                    url, json=body, headers={"x-goog-api-key": s.gemini_api_key}
                )
                resp.raise_for_status()
                return _check(
                    resp.json().get("embedding", {}).get("values"), self.dimensions, self.name
                )

        return await with_retries(call, attempts=s.llm_max_retries, provider=self.name)

    async def embed_text(self, text: str) -> list[float]:
        return await self._embed(text, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(text, "RETRIEVAL_QUERY")


class OpenAICompatibleEmbeddingProvider:
    name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.embedding_base_url or not settings.embedding_model:
            raise ProviderUnavailable("Embedding base URL and model must be configured")
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self.is_external = not settings.embedding_is_local
        self._settings = settings

    async def _embed(self, text: str) -> list[float]:
        s = self._settings
        url = s.embedding_base_url.rstrip("/") + "/embeddings"
        body = {"model": self.model, "input": text[: s.embedding_max_chars]}

        async def call() -> list[float]:
            async with httpx.AsyncClient(timeout=s.embedding_timeout_seconds) as client:
                resp = await client.post(url, json=body, headers=bearer(s.embedding_api_key))
                resp.raise_for_status()
                data = resp.json().get("data") or [{}]
                return _check(data[0].get("embedding"), self.dimensions, self.name)

        return await with_retries(call, attempts=s.llm_max_retries, provider=self.name)

    async def embed_text(self, text: str) -> list[float]:
        return await self._embed(self._settings.embedding_document_prefix + text)

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(self._settings.embedding_query_prefix + text)


_override: EmbeddingProvider | None = None


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    """Test hook: replace the configured provider."""
    global _override
    _override = provider


def embeddings_enabled() -> bool:
    return _override is not None or get_settings().embedding_provider != "disabled"


def get_embedding_provider() -> EmbeddingProvider:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.embedding_provider == "gemini":
        return GeminiEmbeddingProvider(settings)
    if settings.embedding_provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(settings)
    raise ProviderUnavailable("Semantic search is disabled", code="embeddings_disabled")


def ensure_allowed(provider: EmbeddingProvider | object, ai_external_enabled: bool) -> None:
    if getattr(provider, "is_external", True) and not ai_external_enabled:
        raise Forbidden(
            "Sending thoughts to an external AI provider is disabled in settings",
            code="external_ai_disabled",
        )
