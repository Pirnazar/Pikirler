"""Speech recognition providers. Heavy models never run in the API process:
transcription happens in the worker, usually by calling a GPU machine.

* ``remote_whisper``       -- asr_server/ on the GPU box (faster-whisper or the
                              fine-tuned Turkmen Whisper model)
* ``openai_compatible``    -- any /v1/audio/transcriptions endpoint
* ``local_faster_whisper`` -- faster-whisper inside the worker (pip extra)
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderUnavailable
from app.modules.pikirler.integrations.http import ProviderError, bearer, with_retries

SUPPORTED_LANGUAGES = ("tk", "ru", "en", "zh")


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    model: str
    segments: list[dict[str, Any]] = field(default_factory=list)


class TranscriptionProvider(Protocol):
    name: str
    model: str
    is_external: bool

    async def transcribe(
        self, audio_path: str, language: str | None = None
    ) -> TranscriptionResult: ...


def _segments(raw: object) -> list[dict[str, Any]]:
    out = []
    for seg in raw if isinstance(raw, list) else []:
        if isinstance(seg, dict) and "text" in seg:
            out.append(
                {
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "text": str(seg["text"]).strip(),
                }
            )
    return out


class _HttpProvider:
    name = "http"
    endpoint = ""

    def __init__(self, settings: Settings) -> None:
        if not settings.asr_base_url:
            raise ProviderUnavailable("PIKIRLER_ASR_BASE_URL is not set")
        self.model = settings.asr_model or "default"
        self.is_external = not settings.asr_is_local
        self._settings = settings

    def _form(self, language: str | None) -> dict[str, str]:
        raise NotImplementedError

    async def transcribe(self, audio_path: str, language: str | None = None) -> TranscriptionResult:
        s = self._settings
        url = s.asr_base_url.rstrip("/") + self.endpoint
        mime = mimetypes.guess_type(audio_path)[0] or "application/octet-stream"

        async def call() -> TranscriptionResult:
            with open(audio_path, "rb") as fh:  # noqa: ASYNC230 -- streamed by httpx
                files = {"file": (os.path.basename(audio_path), fh, mime)}
                async with httpx.AsyncClient(timeout=s.asr_timeout_seconds) as client:
                    resp = await client.post(
                        url, data=self._form(language), files=files, headers=bearer(s.asr_api_key)
                    )
                    resp.raise_for_status()
                    data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                raise ProviderError("ASR returned an unexpected response")
            return TranscriptionResult(
                text=data["text"].strip(),
                language=data.get("language") or language,
                model=str(data.get("model") or self.model),
                segments=_segments(data.get("segments")),
            )

        return await with_retries(call, attempts=1, provider=self.name)


class RemoteWhisperProvider(_HttpProvider):
    name = "remote_whisper"
    endpoint = "/v1/transcribe"

    def _form(self, language: str | None) -> dict[str, str]:
        return {"language": language} if language else {}


class OpenAICompatibleTranscriptionProvider(_HttpProvider):
    name = "openai_compatible"
    endpoint = "/audio/transcriptions"

    def _form(self, language: str | None) -> dict[str, str]:
        form = {"model": self.model, "response_format": "verbose_json"}
        if language:
            form["language"] = language
        return form


class LocalFasterWhisperProvider:
    name = "local_faster_whisper"
    is_external = False
    _models: dict[str, Any] = {}

    def __init__(self, settings: Settings) -> None:
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise ProviderUnavailable(
                "faster-whisper is not installed (pip install '.[local-asr]')"
            ) from exc
        self.model = settings.asr_model or "large-v3"
        self._settings = settings

    def _load(self) -> Any:
        if self.model not in self._models:
            from faster_whisper import WhisperModel

            self._models[self.model] = WhisperModel(
                self.model,
                device=self._settings.asr_device,
                compute_type=self._settings.asr_compute_type,
            )
        return self._models[self.model]

    async def transcribe(self, audio_path: str, language: str | None = None) -> TranscriptionResult:
        def run() -> TranscriptionResult:
            # No VAD: it shifts timestamps onto the speech-only timeline.
            segments, info = self._load().transcribe(audio_path, language=language, beam_size=5)
            segs = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
            return TranscriptionResult(
                text=" ".join(s["text"] for s in segs).strip(),
                language=info.language,
                model=self.model,
                segments=segs,
            )

        return await asyncio.to_thread(run)


_override: TranscriptionProvider | None = None


def set_transcription_provider(provider: TranscriptionProvider | None) -> None:
    global _override
    _override = provider


def get_transcription_provider() -> TranscriptionProvider:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.asr_provider == "remote_whisper":
        return RemoteWhisperProvider(settings)
    if settings.asr_provider == "openai_compatible":
        return OpenAICompatibleTranscriptionProvider(settings)
    if settings.asr_provider == "local_faster_whisper":
        return LocalFasterWhisperProvider(settings)
    raise ProviderUnavailable(
        "Speech recognition is disabled (PIKIRLER_ASR_PROVIDER=disabled)", code="asr_disabled"
    )


def asr_enabled() -> bool:
    return _override is not None or get_settings().asr_provider != "disabled"
