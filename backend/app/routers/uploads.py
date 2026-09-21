import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user
from app.models import Attachment, User
from app.schemas import AttachmentOut
from app.services import extraction

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

UPLOAD_DIR = Path("/data/uploads")
MAX_BYTES = 25 * 1024 * 1024
STORAGE_QUOTA_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB per user


async def _get_storage_usage(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Return total bytes used by a user's attachments."""
    result = await db.scalar(
        select(func.coalesce(func.sum(Attachment.size_bytes), 0)).where(
            Attachment.user_id == user_id
        )
    )
    return int(result or 0)


def _to_out(record: Attachment) -> AttachmentOut:
    text = record.extracted_text or ""
    return AttachmentOut(
        id=record.id,
        filename=record.filename,
        kind=record.kind,
        size_bytes=record.size_bytes,
        chars_extracted=len(text),
        preview=text[:300] or None,
    )


@router.post("", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is {len(data) // 1024 // 1024}MB; the limit is 25MB",
        )

    kind = extraction.kind_of(file.content_type or "", file.filename or "")
    if kind == "unsupported":
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Supported files: PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, and PNG/JPEG/WebP images",
        )

    # Enforce 5 GB storage quota per user
    current_usage = await _get_storage_usage(db, user.id)
    if current_usage + len(data) > STORAGE_QUOTA_BYTES:
        used_mb = current_usage / (1024 * 1024)
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Storage quota exceeded. You are using {used_mb:.0f} MB of 5120 MB.",
        )

    text = ""
    if kind != "image":
        try:
            text = extraction.extract(data, kind)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix[:12]
    stored = UPLOAD_DIR / f"{uuid.uuid4()}{suffix}"
    stored.write_bytes(data)

    record = Attachment(
        user_id=user.id,
        filename=Path(file.filename or "file").name,
        mime_type=file.content_type or "application/octet-stream",
        kind=kind,
        size_bytes=len(data),
        storage_path=str(stored),
        extracted_text=text or None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _to_out(record)


@router.get("/{attachment_id}", response_model=AttachmentOut)
async def get_attachment(
    attachment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(Attachment, attachment_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return _to_out(record)


@router.get("/me/storage-usage")
async def get_storage_usage(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return current storage usage and quota for the authenticated user."""
    used = await _get_storage_usage(db, user.id)
    return {
        "used_bytes": used,
        "quota_bytes": STORAGE_QUOTA_BYTES,
        "remaining_bytes": max(0, STORAGE_QUOTA_BYTES - used),
    }
