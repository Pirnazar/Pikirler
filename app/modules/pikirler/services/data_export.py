"""Data ownership: export (JSON, Markdown, full archive), import, purge.

The JSON document is the canonical backup format: ids, original timestamps,
threads, revisions, tags (original spelling), projects, relations, attachment
metadata and transcripts. The archive adds Markdown files and the original
attachment bytes. Import is idempotent: rows whose id already exists are skipped.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import uuid
import zipfile
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.exceptions import NotFound, ValidationFailed
from app.modules.pikirler.models import (
    AIUsage,
    Attachment,
    Job,
    Owner,
    Project,
    Tag,
    Thought,
    ThoughtAttachment,
    ThoughtEmbedding,
    ThoughtProject,
    ThoughtRelation,
    ThoughtRevision,
    ThoughtTag,
    Thread,
    VoiceTranscription,
)
from app.modules.pikirler.services import jobs
from app.storage import get_storage

FORMAT = "pikirler-export"
VERSION = 1
EXPORT_JOB = "export_archive"
IMPORT_JOB = "import_archive"
MAX_IMPORT_BYTES = 5 * 1024 * 1024 * 1024


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _rows(objs: Iterable[Any], fields: list[str]) -> list[dict[str, Any]]:
    out = []
    for obj in objs:
        row = {}
        for f in fields:
            v = getattr(obj, f)
            row[f] = (
                _iso(v) if isinstance(v, datetime) else (str(v) if isinstance(v, uuid.UUID) else v)
            )
        out.append(row)
    return out


async def build_export(db: AsyncSession, owner_id: uuid.UUID) -> dict[str, Any]:
    owner = await db.get(Owner, owner_id)
    assert owner is not None

    async def all_of(model: Any, *order: Any) -> list[Any]:
        stmt = select(model).where(model.owner_id == owner_id)
        return list((await db.execute(stmt.order_by(*order) if order else stmt)).scalars())

    thoughts = await all_of(Thought, Thought.created_at, Thought.id)
    tags = await all_of(Tag, Tag.normalized_name)
    tag_names = {t.id: t.name for t in tags}
    revisions: dict[uuid.UUID, list[ThoughtRevision]] = {}
    for r in await all_of(ThoughtRevision, ThoughtRevision.revision_number):
        revisions.setdefault(r.thought_id, []).append(r)
    tag_links: dict[uuid.UUID, list[ThoughtTag]] = {}
    for link in await all_of(ThoughtTag):
        tag_links.setdefault(link.thought_id, []).append(link)
    project_links: dict[uuid.UUID, list[str]] = {}
    for pl in await all_of(ThoughtProject):
        project_links.setdefault(pl.thought_id, []).append(str(pl.project_id))
    att_links: dict[uuid.UUID, list[ThoughtAttachment]] = {}
    for al in await all_of(ThoughtAttachment, ThoughtAttachment.position):
        att_links.setdefault(al.thought_id, []).append(al)

    thought_fields = [
        "id",
        "content",
        "content_format",
        "thought_type",
        "visibility",
        "status",
        "source",
        "parent_id",
        "thread_id",
        "is_bookmarked",
        "is_pinned",
        "occurred_at",
        "created_at",
        "updated_at",
        "deleted_at",
        "version",
    ]
    thought_rows = []
    for t, row in zip(thoughts, _rows(thoughts, thought_fields), strict=True):
        row["tags"] = [
            {"name": tag_names[link.tag_id], "origin": link.origin}
            for link in tag_links.get(t.id, [])
        ]
        row["project_ids"] = project_links.get(t.id, [])
        row["attachment_ids"] = [str(a.attachment_id) for a in att_links.get(t.id, [])]
        row["revisions"] = _rows(
            revisions.get(t.id, []),
            ["revision_number", "previous_content", "new_content", "change_source", "created_at"],
        )
        thought_rows.append(row)

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": utcnow().isoformat(),
        "owner": {
            "email": owner.email,
            "display_name": owner.display_name,
            "timezone": owner.timezone,
        },
        "projects": _rows(
            await all_of(Project, Project.created_at),
            ["id", "name", "description", "status", "external_ref", "created_at", "updated_at"],
        ),
        "tags": _rows(tags, ["id", "name", "normalized_name", "created_at"]),
        "threads": _rows(
            await all_of(Thread, Thread.created_at),
            ["id", "root_thought_id", "title", "created_at", "last_activity_at"],
        ),
        "thoughts": thought_rows,
        "relations": _rows(
            await all_of(ThoughtRelation, ThoughtRelation.created_at),
            [
                "id",
                "source_thought_id",
                "target_thought_id",
                "relation_type",
                "created_by",
                "status",
                "confidence",
                "rationale",
                "created_at",
                "confirmed_at",
            ],
        ),
        "attachments": _rows(
            [
                a
                for a in await all_of(Attachment, Attachment.created_at)
                if a.uploaded_at is not None and a.deleted_at is None
            ],
            [
                "id",
                "original_filename",
                "safe_filename",
                "mime_type",
                "kind",
                "file_size",
                "checksum",
                "width",
                "height",
                "duration_seconds",
                "created_at",
                "uploaded_at",
            ],
        ),
        "transcriptions": _rows(
            await all_of(VoiceTranscription, VoiceTranscription.created_at),
            [
                "id",
                "attachment_id",
                "thought_id",
                "status",
                "requested_language",
                "detected_language",
                "provider",
                "model",
                "raw_text",
                "edited_text",
                "segments",
                "created_at",
                "completed_at",
                "edited_at",
            ],
        ),
    }


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def to_json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, indent=1, default=_default).encode()


def thought_markdown(t: dict[str, Any], tz_name: str) -> tuple[str, str]:
    created = datetime.fromisoformat(t["created_at"])
    path = f"{created:%Y}/{created:%m}/{created:%Y-%m-%d}-{t['id'][:8]}.md"
    front = {
        "id": t["id"],
        "created_at": t["created_at"],
        "updated_at": t["updated_at"],
        "occurred_at": t["occurred_at"],
        "type": t["thought_type"],
        "status": t["status"],
        "tags": [x["name"] for x in t["tags"]],
        "projects": t["project_ids"],
        "thread_id": t["thread_id"],
        "parent_id": t["parent_id"],
        "bookmarked": t["is_bookmarked"],
        "pinned": t["is_pinned"],
        "attachments": t["attachment_ids"],
        "deleted_at": t["deleted_at"],
    }
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()]
    lines += ["---", "", t["content"], ""]
    return path, "\n".join(lines)


def markdown_zip(document: dict[str, Any], target: Any) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in document["thoughts"]:
            path, body = thought_markdown(t, document["owner"]["timezone"])
            zf.writestr(f"markdown/{path}", body)


# --- full archive (background job) ----------------------------------------------


def archive_key(owner_id: uuid.UUID, job_id: uuid.UUID) -> str:
    return f"owners/{owner_id}/exports/{job_id}/archive.zip"


async def request_export(db: AsyncSession, owner_id: uuid.UUID) -> uuid.UUID:
    job_id = await jobs.enqueue(
        db, EXPORT_JOB, {"owner_id": str(owner_id)}, owner_id=owner_id, max_attempts=2
    )
    await db.commit()
    return job_id


async def run_export_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    owner_id = uuid.UUID(payload["owner_id"])
    job_id = uuid.UUID(payload["_job_id"])
    document = await build_export(db, owner_id)
    storage = get_storage()
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr("data.json", to_json_bytes(document))
            for t in document["thoughts"]:
                md_path, body = thought_markdown(t, document["owner"]["timezone"])
                zf.writestr(f"markdown/{md_path}", body)
            atts = {
                a.id: a
                for a in (
                    await db.execute(
                        select(Attachment).where(
                            Attachment.owner_id == owner_id,
                            Attachment.uploaded_at.isnot(None),
                            Attachment.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            }
            for att in atts.values():
                with zf.open(
                    f"attachments/{att.id}/{att.safe_filename}", "w", force_zip64=True
                ) as out:
                    async for chunk in storage.open_stream(att.storage_key):
                        out.write(chunk)
        size = os.path.getsize(path)
        with open(path, "rb") as fh:  # noqa: ASYNC230
            await storage.put_file(archive_key(owner_id, job_id), fh, "application/zip")
    finally:
        os.unlink(path)
    return {
        "storage_key": archive_key(owner_id, job_id),
        "size": size,
        "thoughts": len(document["thoughts"]),
        "attachments": len(document["attachments"]),
    }


async def export_archive_location(
    db: AsyncSession, owner_id: uuid.UUID, job_id: uuid.UUID
) -> tuple[str, int]:
    job = await jobs.get_job(db, owner_id, job_id)
    if job.kind != EXPORT_JOB or job.status != "succeeded" or not job.result:
        raise NotFound(
            "Export is not ready", code="export_not_ready", details={"status": job.status}
        )
    key = archive_key(owner_id, job_id)
    info = await get_storage().head(key)
    if info is None:
        raise NotFound("Export archive expired", code="export_missing")
    return key, info.size


# --- import ----------------------------------------------------------------------


def import_key(owner_id: uuid.UUID, upload_id: uuid.UUID, kind: str) -> str:
    return f"owners/{owner_id}/imports/{upload_id}/source.{kind}"


async def request_import(
    db: AsyncSession, owner_id: uuid.UUID, filename: str, stream: Any
) -> uuid.UUID:
    kind = "zip" if filename.lower().endswith(".zip") else "json"
    upload_id = uuid.uuid4()
    key = import_key(owner_id, upload_id, kind)
    await get_storage().put_stream(key, stream, "application/octet-stream", MAX_IMPORT_BYTES)
    job_id = await jobs.enqueue(
        db,
        IMPORT_JOB,
        {"owner_id": str(owner_id), "key": key, "kind": kind},
        owner_id=owner_id,
        max_attempts=2,
    )
    await db.commit()
    return job_id


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _topological(thoughts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {t["id"]: t for t in thoughts}
    done: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for t in sorted(thoughts, key=lambda x: x["created_at"]):
        chain = []
        node: dict[str, Any] | None = t
        while node is not None and node["id"] not in done and node not in chain:
            chain.append(node)
            parent_id = node.get("parent_id")
            node = by_id.get(parent_id) if parent_id else None
        for item in reversed(chain):
            done.add(item["id"])
            ordered.append(item)
    return ordered


async def import_document(
    db: AsyncSession, owner_id: uuid.UUID, doc: dict[str, Any], files: zipfile.ZipFile | None = None
) -> dict[str, int]:
    if doc.get("format") != FORMAT or doc.get("version") != VERSION:
        raise ValidationFailed("Not a Pikirler export (format/version)", code="invalid_import")
    from app.core.text import build_search_document, normalize_tag
    from app.modules.pikirler.services.tags import upsert_tags

    stats = {"thoughts": 0, "skipped": 0, "attachments": 0}
    for p in doc.get("projects", []):
        await db.execute(
            insert(Project)
            .values(
                id=_uuid(p["id"]),
                owner_id=owner_id,
                name=p["name"],
                description=p["description"],
                status=p["status"],
                external_ref=p["external_ref"],
                created_at=_dt(p["created_at"]),
            )
            .on_conflict_do_nothing()
        )
    for th in doc.get("threads", []):
        await db.execute(
            insert(Thread)
            .values(
                id=_uuid(th["id"]),
                owner_id=owner_id,
                title=th["title"],
                created_at=_dt(th["created_at"]),
                last_activity_at=_dt(th["last_activity_at"]),
            )
            .on_conflict_do_nothing()
        )

    storage = get_storage()
    from app.modules.pikirler.services.media import storage_key

    for a in doc.get("attachments", []):
        aid = uuid.UUID(a["id"])
        if await db.get(Attachment, aid) is not None:
            continue
        member = f"attachments/{a['id']}/{a['safe_filename']}"
        if files is None or member not in files.namelist():
            continue
        key = storage_key(owner_id, aid, "original")
        with files.open(member) as fh:
            await storage.put_file(key, fh, a["mime_type"])
        db.add(
            Attachment(
                id=aid,
                owner_id=owner_id,
                storage_key=key,
                original_filename=a["original_filename"],
                safe_filename=a["safe_filename"],
                mime_type=a["mime_type"],
                declared_mime_type=a["mime_type"],
                kind=a["kind"],
                file_size=a["file_size"],
                checksum=a["checksum"],
                processing_status="uploaded",
                width=a.get("width"),
                height=a.get("height"),
                duration_seconds=a.get("duration_seconds"),
                created_at=_dt(a["created_at"]),
                uploaded_at=_dt(a["uploaded_at"]),
            )
        )
        await jobs.enqueue(
            db,
            "process_attachment",
            {"attachment_id": str(aid)},
            owner_id=owner_id,
            dedupe_key=f"process:{aid}",
        )
        stats["attachments"] += 1
    await db.flush()

    existing_atts = set(
        (await db.execute(select(Attachment.id).where(Attachment.owner_id == owner_id))).scalars()
    )
    existing_projects = set(
        (await db.execute(select(Project.id).where(Project.owner_id == owner_id))).scalars()
    )
    imported: set[str] = set()
    for t in _topological(doc.get("thoughts", [])):
        tid = uuid.UUID(t["id"])
        if await db.get(Thought, tid) is not None:
            stats["skipped"] += 1
            continue
        parent = t.get("parent_id")
        exists_parent = (
            parent is None
            or parent in imported
            or (await db.get(Thought, uuid.UUID(parent)) is not None)
        )
        thought = Thought(
            id=tid,
            owner_id=owner_id,
            content=t["content"],
            content_format=t["content_format"],
            thought_type=t["thought_type"],
            status=t["status"],
            source="import",
            parent_id=_uuid(parent) if exists_parent else None,
            thread_id=_uuid(t.get("thread_id")),
            is_bookmarked=t["is_bookmarked"],
            is_pinned=t["is_pinned"],
            occurred_at=_dt(t["occurred_at"]),
            created_at=_dt(t["created_at"]),
            updated_at=_dt(t["updated_at"]),
            deleted_at=_dt(t["deleted_at"]),
            version=t["version"],
            search_document=build_search_document(
                t["content"], " ".join(x["name"] for x in t["tags"])
            ),
        )
        db.add(thought)
        await db.flush()
        imported.add(t["id"])
        tags = await upsert_tags(db, owner_id, [x["name"] for x in t["tags"]])
        for x in t["tags"]:
            tag = tags.get(normalize_tag(x["name"]))
            if tag:
                await db.execute(
                    insert(ThoughtTag)
                    .values(thought_id=tid, tag_id=tag.id, owner_id=owner_id, origin=x["origin"])
                    .on_conflict_do_nothing()
                )
        for pid in t["project_ids"]:
            if uuid.UUID(pid) in existing_projects:
                db.add(ThoughtProject(thought_id=tid, project_id=uuid.UUID(pid), owner_id=owner_id))
        for pos, aid in enumerate(t["attachment_ids"]):
            if uuid.UUID(aid) in existing_atts:
                db.add(
                    ThoughtAttachment(
                        thought_id=tid,
                        attachment_id=uuid.UUID(aid),
                        owner_id=owner_id,
                        position=pos,
                    )
                )
        for r in t["revisions"]:
            db.add(
                ThoughtRevision(
                    owner_id=owner_id,
                    thought_id=tid,
                    revision_number=r["revision_number"],
                    previous_content=r["previous_content"],
                    new_content=r["new_content"],
                    change_source=r["change_source"],
                    created_at=_dt(r["created_at"]),
                )
            )
        stats["thoughts"] += 1
    await db.flush()
    for th in doc.get("threads", []):
        if th.get("root_thought_id"):
            await db.execute(
                update(Thread)
                .where(Thread.id == uuid.UUID(th["id"]), Thread.owner_id == owner_id)
                .values(root_thought_id=uuid.UUID(th["root_thought_id"]))
            )
    known = set(
        (await db.execute(select(Thought.id).where(Thought.owner_id == owner_id))).scalars()
    )
    for r in doc.get("relations", []):
        src, dst = uuid.UUID(r["source_thought_id"]), uuid.UUID(r["target_thought_id"])
        if src in known and dst in known:
            await db.execute(
                insert(ThoughtRelation)
                .values(
                    id=uuid.UUID(r["id"]),
                    owner_id=owner_id,
                    source_thought_id=src,
                    target_thought_id=dst,
                    relation_type=r["relation_type"],
                    created_by=r["created_by"],
                    status=r["status"],
                    confidence=r["confidence"],
                    rationale=r["rationale"],
                    created_at=_dt(r["created_at"]),
                    confirmed_at=_dt(r["confirmed_at"]),
                )
                .on_conflict_do_nothing()
            )
    for v in doc.get("transcriptions", []):
        if uuid.UUID(v["attachment_id"]) in existing_atts:
            await db.execute(
                insert(VoiceTranscription)
                .values(
                    id=uuid.UUID(v["id"]),
                    owner_id=owner_id,
                    attachment_id=uuid.UUID(v["attachment_id"]),
                    thought_id=_uuid(v["thought_id"]) if _uuid(v["thought_id"]) in known else None,
                    status=v["status"],
                    requested_language=v["requested_language"],
                    detected_language=v["detected_language"],
                    provider=v["provider"],
                    model=v["model"],
                    raw_text=v["raw_text"],
                    edited_text=v["edited_text"],
                    segments=v["segments"] or [],
                    created_at=_dt(v["created_at"]),
                    completed_at=_dt(v["completed_at"]),
                    edited_at=_dt(v["edited_at"]),
                )
                .on_conflict_do_nothing()
            )
    await db.commit()
    return stats


async def run_import_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    owner_id = uuid.UUID(payload["owner_id"])
    storage = get_storage()
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        await storage.download_to(payload["key"], path)
        if payload["kind"] == "zip":
            with zipfile.ZipFile(path) as zf:
                doc = json.loads(zf.read("data.json"))
                stats = await import_document(db, owner_id, doc, zf)
        else:
            with open(path, "rb") as fh:  # noqa: ASYNC230
                doc = json.loads(fh.read())
            stats = await import_document(db, owner_id, doc)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        from app.modules.pikirler.tasks.runner import PermanentJobError

        raise PermanentJobError(f"unreadable import file ({type(exc).__name__})") from exc
    finally:
        os.unlink(path)
    await storage.delete(payload["key"])
    from app.modules.pikirler.services.thoughts import refresh_search_documents

    ids = list((await db.execute(select(Thought.id).where(Thought.owner_id == owner_id))).scalars())
    await refresh_search_documents(db, owner_id, ids)
    await db.commit()
    return stats


# --- purge -------------------------------------------------------------------------

PURGE_PHRASE = "DELETE ALL MY THOUGHTS"


async def purge_all(db: AsyncSession, owner_id: uuid.UUID) -> dict[str, int]:
    """Irreversibly delete every thought, file and derived record of the owner.
    The account itself (login) is kept."""
    storage = get_storage()
    atts = list(
        (await db.execute(select(Attachment).where(Attachment.owner_id == owner_id))).scalars()
    )
    for att in atts:
        for key in (att.storage_key, att.preview_key, att.thumbnail_key):
            if key:
                await storage.delete(key)
    export_keys = [
        archive_key(owner_id, j)
        for j in (
            await db.execute(select(Job.id).where(Job.owner_id == owner_id, Job.kind == EXPORT_JOB))
        ).scalars()
    ]
    for key in export_keys:
        await storage.delete(key)
    counts = {}
    await db.execute(update(Thought).where(Thought.owner_id == owner_id).values(parent_id=None))
    for model in (
        ThoughtRelation,
        ThoughtEmbedding,
        VoiceTranscription,
        ThoughtAttachment,
        ThoughtTag,
        ThoughtProject,
        ThoughtRevision,
    ):
        await db.execute(delete(model).where(model.owner_id == owner_id))
    await db.execute(update(Thread).where(Thread.owner_id == owner_id).values(root_thought_id=None))
    for name, table in (
        ("thoughts", Thought),
        ("threads", Thread),
        ("tags", Tag),
        ("projects", Project),
        ("attachments", Attachment),
        ("jobs", Job),
        ("ai_usage", AIUsage),
    ):
        result = await db.execute(delete(table).where(table.owner_id == owner_id))
        counts[name] = result.rowcount  # type: ignore[attr-defined]
    await db.commit()
    return counts


def markdown_bytes(document: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    markdown_zip(document, buf)
    return buf.getvalue()
