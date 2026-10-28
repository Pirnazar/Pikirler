"""Reference ASR server for the GPU machine (e.g. the RTX 2060 box).

Pikirler's worker calls it with PIKIRLER_ASR_PROVIDER=remote_whisper:

    POST /v1/transcribe   multipart: file, language (tk|ru|en|zh, optional)
    -> {"text", "language", "model", "segments": [{"start", "end", "text"}]}

Run:
    pip install -r requirements.txt
    ASR_API_KEY=... ASR_MODEL=large-v3 uvicorn app:app --host 0.0.0.0 --port 8900

ASR_MODEL can be a faster-whisper size name, a Hugging Face repo id, or a path
to a CTranslate2 model directory -- e.g. the Turkmen Whisper fine-tune merged
and converted with ct2-transformers-converter. Speech that mixes languages is
transcribed as spoken: nothing is translated or transliterated.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import tempfile

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from faster_whisper import WhisperModel

API_KEY = os.environ.get("ASR_API_KEY", "")
MODEL_NAME = os.environ.get("ASR_MODEL", "large-v3")
DEVICE = os.environ.get("ASR_DEVICE", "auto")
COMPUTE_TYPE = os.environ.get("ASR_COMPUTE_TYPE", "int8_float16")
MAX_BYTES = int(os.environ.get("ASR_MAX_BYTES", str(300 * 1024 * 1024)))
LANGUAGES = {"tk", "ru", "en", "zh"}

app = FastAPI(title="Pikirler ASR")
_model: WhisperModel | None = None
_lock = asyncio.Lock()  # one transcription at a time: a small GPU has room for one model run


def model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    return _model


def check_key(authorization: str = Header(default="")) -> None:
    if not API_KEY:
        raise HTTPException(500, "ASR_API_KEY is not configured")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), API_KEY.encode()):
        raise HTTPException(401, "invalid key")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/v1/transcribe", dependencies=[Depends(check_key)])
async def transcribe(file: UploadFile = File(...), language: str | None = Form(default=None)
                     ) -> dict[str, object]:
    if language is not None and language not in LANGUAGES:
        raise HTTPException(422, "unsupported language")
    suffix = os.path.splitext(file.filename or "")[1][:10]
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        size = 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise HTTPException(413, "file too large")
            tmp.write(chunk)
        tmp.flush()

        def run() -> dict[str, object]:
            # No VAD filter: it shifts timestamps to the speech-only timeline.
            segments, info = model().transcribe(tmp.name, language=language, beam_size=5)
            segs = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
            return {"text": " ".join(s["text"] for s in segs).strip(), "language": info.language,
                    "model": MODEL_NAME, "segments": segs}

        async with _lock:
            return await asyncio.to_thread(run)
