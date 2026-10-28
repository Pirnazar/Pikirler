from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select, update

from app.core.database import get_database, utcnow
from app.modules.pikirler.integrations import asr
from app.modules.pikirler.models import Job
from app.modules.pikirler.services import jobs
from app.modules.pikirler.tasks import runner
from tests.conftest import new_thought
from tests.fakes import FakeASR
from tests.helpers import run_jobs, upload, wav_bytes


async def _voice_note(client: httpx.AsyncClient, language: str | None = "tk") -> dict:  # type: ignore[type-arg]
    data = {"language": language} if language else {}
    resp = await client.post(
        "/voice/notes",
        data={**data, "tags": "ses"},
        files={"file": ("note.wav", wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def test_voice_note_saved_even_when_asr_disabled(client: httpx.AsyncClient) -> None:
    body = await _voice_note(client)
    assert body["transcription"] is None and body["transcription_unavailable"] == "asr_disabled"
    assert len(body["thought"]["attachments"]) == 1
    resp = await client.post(
        "/voice/transcriptions", json={"attachment_id": body["thought"]["attachments"][0]["id"]}
    )
    assert resp.status_code == 503


async def test_voice_transcription_flow(client: httpx.AsyncClient) -> None:
    fake = FakeASR("Ýyldyrym Market barada pikir, и ещё 人工智能")
    asr.set_transcription_provider(fake)
    body = await _voice_note(client)
    vt = body["transcription"]
    assert vt["status"] == "queued" and vt["raw_text"] is None
    statuses = await run_jobs()
    assert "succeeded" in statuses
    done = (await client.get(f"/voice/transcriptions/{vt['id']}")).json()
    assert done["status"] == "completed" and done["raw_text"].startswith("Ýyldyrym")
    assert done["requested_language"] == "tk" and done["ai_generated"] is True
    assert fake.calls[0][1] == "tk"

    edited = (
        await client.patch(
            f"/voice/transcriptions/{vt['id']}",
            json={"edited_text": "Ýyldyrym Market barada täze pikir"},
        )
    ).json()
    assert edited["raw_text"].startswith("Ýyldyrym Market barada pikir")  # original kept
    assert edited["text"] == "Ýyldyrym Market barada täze pikir"
    # The transcript makes the voice thought searchable; the thought text is untouched.
    hits = (await client.get("/search", params={"q": "täze"})).json()["results"]
    assert [h["thought_id"] for h in hits] == [body["thought"]["id"]]
    thought = (await client.get(f"/thoughts/{body['thought']['id']}")).json()
    assert thought["content"] == ""
    assert thought["attachments"][0]["transcription"]["text"].endswith("täze pikir")


async def test_transcription_of_non_audio_rejected(client: httpx.AsyncClient) -> None:
    asr.set_transcription_provider(FakeASR())
    pdf = b"%PDF-1.4\n%%EOF\n"
    att = (await upload(client, pdf, "a.pdf", "application/pdf")).json()
    resp = await client.post("/voice/transcriptions", json={"attachment_id": att["id"]})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "not_audio"


async def test_asr_failure_retries_then_marks_failed(client: httpx.AsyncClient, db) -> None:  # type: ignore[no-untyped-def]
    fake = FakeASR(fail=True)
    asr.set_transcription_provider(fake)
    body = await _voice_note(client)
    vt_id = body["transcription"]["id"]
    job_id = uuid.UUID(body["transcription"]["job_id"])
    for _ in range(10):
        await db.execute(update(Job).where(Job.id == job_id).values(run_after=utcnow()))
        await db.commit()
        status = await runner.execute(job_id)
        if status == "dead":
            break
    job = (await client.get(f"/jobs/{job_id}")).json()
    assert job["status"] == "dead" and job["attempts"] == job["max_attempts"]
    assert "ASR box offline" in job["last_error"]
    vt = (await client.get(f"/voice/transcriptions/{vt_id}")).json()
    assert vt["status"] == "failed" and vt["error"]
    # The thought itself is untouched by the failure.
    assert (await client.get(f"/thoughts/{body['thought']['id']}")).status_code == 200

    # Manual retry after the ASR box is back.
    fake.fail = False
    retried = await client.post(f"/jobs/{job_id}/retry")
    assert retried.status_code == 200
    assert await runner.execute(job_id) == "succeeded"
    vt = (await client.get(f"/voice/transcriptions/{vt_id}")).json()
    assert vt["status"] == "completed"


async def test_job_runs_once_even_if_delivered_twice(client: httpx.AsyncClient, db) -> None:  # type: ignore[no-untyped-def]
    asr.set_transcription_provider(FakeASR())
    body = await _voice_note(client)
    job_id = uuid.UUID(body["transcription"]["job_id"])
    assert await runner.execute(job_id) == "succeeded"
    assert await runner.execute(job_id) == "skipped"


async def test_dedupe_key_prevents_duplicate_jobs(client: httpx.AsyncClient, db) -> None:  # type: ignore[no-untyped-def]
    t = await new_thought(client, "x")
    first = await jobs.enqueue(
        db, "embed_thought", {"thought_id": t["id"]}, owner_id=None, dedupe_key=f"embed:{t['id']}"
    )
    second = await jobs.enqueue(
        db, "embed_thought", {"thought_id": t["id"]}, owner_id=None, dedupe_key=f"embed:{t['id']}"
    )
    await db.commit()
    assert first == second


async def test_stale_running_job_is_recovered(client: httpx.AsyncClient, db) -> None:  # type: ignore[no-untyped-def]
    asr.set_transcription_provider(FakeASR())
    body = await _voice_note(client)
    job_id = uuid.UUID(body["transcription"]["job_id"])
    # Simulate a worker that died mid-job.
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status="running", attempts=1, locked_until=utcnow() - timedelta(minutes=1))
    )
    await db.commit()
    assert await jobs.dispatch_due(db) >= 1
    assert await runner.execute(job_id) == "succeeded"


async def test_outbox_job_committed_with_data_and_dispatched(client: httpx.AsyncClient) -> None:
    from app.core.redis import get_redis

    asr.set_transcription_provider(FakeASR())
    body = await _voice_note(client)
    job_id = body["transcription"]["job_id"]
    async with get_database().sessionmaker() as db:
        job = (await db.execute(select(Job).where(Job.id == uuid.UUID(job_id)))).scalar_one()
        assert job.status == "pending" and job.enqueued_at is not None  # pushed after commit
        # Lose the queue entirely (Redis flushed): the dispatcher re-enqueues from the table.
        await get_redis().flushdb()
        await db.execute(
            update(Job).where(Job.id == job.id).values(enqueued_at=utcnow() - timedelta(minutes=5))
        )
        await db.commit()
        assert await jobs.dispatch_due(db) >= 1
    keys = await get_redis().keys("arq:job:*")
    assert any(job_id in k for k in keys)
