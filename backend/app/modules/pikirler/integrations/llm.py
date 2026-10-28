"""LLM providers. Gemini is one option, never a hard dependency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderUnavailable
from app.modules.pikirler.integrations.http import ProviderError, bearer, with_retries


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class GenerationContext:
    """Per-call options. ``json_output`` asks for a single JSON object."""

    json_output: bool = True
    temperature: float = 0.2
    max_output_tokens: int = 2048


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    name: str
    model: str
    is_external: bool

    async def generate(
        self, messages: list[LLMMessage], context: GenerationContext
    ) -> LLMResponse: ...


class GeminiLLMProvider:
    name = "gemini"
    is_external = True

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key or not settings.llm_model:
            raise ProviderUnavailable("Gemini API key and PIKIRLER_LLM_MODEL must be set")
        self.model = settings.llm_model
        self._settings = settings

    async def generate(self, messages: list[LLMMessage], context: GenerationContext) -> LLMResponse:
        s = self._settings
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        config: dict[str, Any] = {
            "temperature": context.temperature,
            "maxOutputTokens": context.max_output_tokens,
        }
        if context.json_output:
            config["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{s.gemini_base_url}/models/{self.model}:generateContent"

        async def call() -> LLMResponse:
            async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as client:
                resp = await client.post(
                    url, json=body, headers={"x-goog-api-key": s.gemini_api_key}
                )
                resp.raise_for_status()
                data = resp.json()
            try:
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
            except (KeyError, IndexError, TypeError) as exc:
                reason = (data.get("promptFeedback") or {}).get("blockReason") or "empty"
                raise ProviderError(f"Gemini returned no content ({reason})") from exc
            return LLMResponse(text=text, provider=self.name, model=self.model)

        return await with_retries(call, attempts=s.llm_max_retries, provider=self.name)


class OpenAICompatibleLLMProvider:
    """/v1/chat/completions: Ollama, LM Studio, vLLM, llama.cpp server, ..."""

    name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_base_url or not settings.llm_model:
            raise ProviderUnavailable("LLM base URL and model must be configured")
        self.model = settings.llm_model
        self.is_external = not settings.llm_is_local
        self._settings = settings

    async def generate(self, messages: list[LLMMessage], context: GenerationContext) -> LLMResponse:
        s = self._settings
        body = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": context.temperature,
            "max_tokens": context.max_output_tokens,
        }
        url = s.llm_base_url.rstrip("/") + "/chat/completions"

        async def call() -> LLMResponse:
            async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as client:
                resp = await client.post(url, json=body, headers=bearer(s.llm_api_key))
                resp.raise_for_status()
                data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderError("LLM returned no content") from exc
            return LLMResponse(text=text, provider=self.name, model=self.model)

        return await with_retries(call, attempts=s.llm_max_retries, provider=self.name)


_override: LLMProvider | None = None


def set_llm_provider(provider: LLMProvider | None) -> None:
    global _override
    _override = provider


def get_llm_provider() -> LLMProvider:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.llm_provider == "gemini":
        return GeminiLLMProvider(settings)
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleLLMProvider(settings)
    raise ProviderUnavailable(
        "AI features are disabled (PIKIRLER_LLM_PROVIDER=disabled)", code="ai_disabled"
    )
