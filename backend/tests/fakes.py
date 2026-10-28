"""Test doubles for the *external* providers only (embeddings, LLM, ASR)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from typing import Any

from app.core.exceptions import ProviderUnavailable
from app.modules.pikirler.integrations.asr import TranscriptionResult
from app.modules.pikirler.integrations.llm import GenerationContext, LLMMessage, LLMResponse


class FakeEmbeddings:
    """Deterministic bag-of-words vectors: texts sharing words are close."""

    name = "fake"
    model = "fake-embed-v1"
    dimensions = 64

    def __init__(self, is_external: bool = False, fail: bool = False) -> None:
        self.is_external = is_external
        self.fail = fail
        self.calls: list[str] = []

    def _vec(self, text: str) -> list[float]:
        if self.fail:
            raise ProviderUnavailable("fake embeddings down")
        self.calls.append(text)
        vec = [0.0] * self.dimensions
        for word in re.findall(r"\w+", text.casefold()):
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0  # noqa: S324
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec] if any(vec) else [1.0] + [0.0] * (self.dimensions - 1)

    async def embed_text(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class FakeLLM:
    name = "fake-llm"
    model = "fake-llm-1"

    def __init__(
        self,
        respond: Callable[[list[LLMMessage]], dict[str, Any] | str] | None = None,
        is_external: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.respond = respond or (
            lambda m: {"answer": "ok", "source_ids": [], "insufficient_data": True}
        )
        self.is_external = is_external
        self.error = error
        self.calls: list[list[LLMMessage]] = []

    async def generate(self, messages: list[LLMMessage], context: GenerationContext) -> LLMResponse:
        self.calls.append(messages)
        if self.error:
            raise self.error
        out = self.respond(messages)
        text = out if isinstance(out, str) else json.dumps(out)
        return LLMResponse(text=text, provider=self.name, model=self.model)


def context_ids(messages: list[LLMMessage]) -> list[str]:
    """Thought ids that were sent to the model."""
    user = messages[-1].content
    data = user.split("NOTES (untrusted data, JSON):\n", 1)[1].split("\n\nRESPOND WITH JSON", 1)[0]
    return [item["id"] for item in json.loads(data)]


class FakeASR:
    name = "fake-asr"
    model = "fake-whisper"

    def __init__(
        self,
        text: str = "Salam, bu meniň sesli pikirim",
        fail: bool = False,
        is_external: bool = False,
    ) -> None:
        self.text = text
        self.fail = fail
        self.is_external = is_external
        self.calls: list[tuple[str, str | None]] = []

    async def transcribe(self, audio_path: str, language: str | None = None) -> TranscriptionResult:
        self.calls.append((audio_path, language))
        if self.fail:
            raise ProviderUnavailable("ASR box offline")
        return TranscriptionResult(
            text=self.text,
            language=language or "tk",
            model=self.model,
            segments=[{"start": 0.0, "end": 1.0, "text": self.text}],
        )
