"""Knowledge base endpoints — upload, list, delete school documents.

Documents uploaded here are chunked, embedded and stored in pgvector so the
chat assistant can answer questions using school-specific information when
the user toggles "Use knowledge base".
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require, write_audit
from app.models import Document, DocumentChunk, User
from app.rbac import Permission, has_permission
from app.services import extraction
from app.services.errors import safe_message
from app.services.embeddings import embed_document

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

UPLOAD_DIR = Path("/data/knowledge")
MAX_BYTES = 25 * 1024 * 1024


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    user: User = Depends(require(Permission.KNOWLEDGE_UPLOAD)),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 25MB limit")

    kind = extraction.kind_of(file.content_type or "", file.filename or "")
    if kind in ("image", "unsupported"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Knowledge base accepts text documents: PDF, DOCX, PPTX, XLSX, CSV, TXT",
        )

    try:
        text = extraction.extract(data, kind)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix[:12]
    stored = UPLOAD_DIR / f"{uuid.uuid4()}{suffix}"
    stored.write_bytes(data)

    doc = Document(
        uploaded_by=user.id,
        filename=Path(file.filename or "document").name,
        mime_type=file.content_type or "application/octet-stream",
        status="processing",
        storage_path=str(stored),
    )
    db.add(doc)
    await db.flush()

    try:
        chunk_count = await embed_document(db, doc, text)
    except Exception as exc:
        doc.status = "error"
        await db.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, safe_message(exc, action="Indexing the document")
        )

    await db.commit()

    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "status": doc.status,
        "chunk_count": chunk_count,
    }


@router.get("/documents")
async def list_documents(
    user: User = Depends(require(Permission.AI_KNOWLEDGE)),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(Document).order_by(desc(Document.created_at)).limit(200)
    )
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "uploaded_by": str(d.uploaded_by),
            "created_at": d.created_at.isoformat(),
        }
        for d in rows
    ]


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    user: User = Depends(require(Permission.KNOWLEDGE_UPLOAD)),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    # KNOWLEDGE_UPLOAD alone is not enough to remove someone else's document:
    # every teacher holds it, so without this any of them could wipe the
    # school's policy library. Uploaders manage their own; admins manage all.
    if doc.uploaded_by != user.id and not has_permission(user.role, Permission.USERS_WRITE):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only remove documents you uploaded. Ask an administrator for others.",
        )

    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
    )
    # The extracted copy on disk outlives the row otherwise.
    stored = Path(doc.storage_path) if doc.storage_path else None
    filename = doc.filename
    await db.delete(doc)
    await db.commit()

    if stored is not None and stored.is_file():
        stored.unlink(missing_ok=True)

    await write_audit(
        db, actor_id=user.id, action="knowledge.delete", target=filename
    )
    await db.commit()


@router.get("/stats")
async def knowledge_stats(
    user: User = Depends(require(Permission.AI_KNOWLEDGE)),
    db: AsyncSession = Depends(get_db),
):
    doc_count = await db.scalar(select(func.count(Document.id)).where(Document.status == "ready"))
    chunk_count = await db.scalar(select(func.count(DocumentChunk.id)))
    return {
        "documents": doc_count or 0,
        "chunks": chunk_count or 0,
    }
