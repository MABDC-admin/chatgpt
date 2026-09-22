import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, require
from app.models import Attachment, GeneratedImage, User
from app.rbac import Permission
from app.schemas import FileItem, FileListResponse

router = APIRouter(prefix="/api/files", tags=["files"])
UPLOAD_DIR = Path("/data/uploads")
IMAGE_DIR = Path("/data/images")


@router.get("", response_model=FileListResponse)
async def list_files(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    kind: str | None = Query(None, description="Filter by file kind (image/pdf/docx/etc)"),
    source_type: str | None = Query(None, description="uploaded or generated"),
    search: str | None = Query(None, description="Search filename"),
    sort_by: str = Query("created_at", regex="^(created_at|filename|size_bytes)$"),
    sort_dir: str = Query("desc", regex="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """List all files belonging to the current user (uploads + generated images)."""
    # --- Uploaded attachments ---
    attach_q = select(
        Attachment.id.label("file_id"),
        Attachment.filename,
        Attachment.mime_type,
        Attachment.kind,
        Attachment.size_bytes,
        Attachment.storage_path,
        Attachment.source_type,
        Attachment.conversation_id,
        Attachment.message_id,
        Attachment.created_at,
        Attachment.thumbnail_path,
        sa.literal("uploaded").label("category"),
    ).where(Attachment.user_id == user.id)

    if kind:
        attach_q = attach_q.where(Attachment.kind == kind)
    if source_type:
        attach_q = attach_q.where(Attachment.source_type == source_type)
    if search:
        attach_q = attach_q.where(Attachment.filename.ilike(f"%{search}%"))

    # --- Generated images (exclude those already represented as Attachments) ---
    gen_q = select(
        GeneratedImage.id.label("file_id"),
        GeneratedImage.prompt.label("filename"),
        sa.literal("image/png").label("mime_type"),
        sa.literal("image").label("kind"),
        sa.cast(0, sa.Integer).label("size_bytes"),  # size unknown without stat
        GeneratedImage.file_path.label("storage_path"),
        sa.literal("generated").label("source_type"),
        sa.cast(None, sa.Uuid).label("conversation_id"),
        sa.cast(None, sa.Uuid).label("message_id"),
        GeneratedImage.created_at,
        sa.cast(None, sa.String).label("thumbnail_path"),
        sa.literal("generated").label("category"),
    ).where(
        GeneratedImage.user_id == user.id,
        ~GeneratedImage.file_path.in_(
            select(Attachment.storage_path).where(
                Attachment.user_id == user.id,
                Attachment.source_type == "generated",
            )
        ),
    )

    if kind and kind != "image":
        gen_q = gen_q.where(sa.false())  # exclude generated when filtering non-image
    if source_type and source_type != "generated":
        gen_q = gen_q.where(sa.false())
    if search:
        gen_q = gen_q.where(GeneratedImage.prompt.ilike(f"%{search}%"))

    # Union both sources
    union_subq = attach_q.union_all(gen_q).subquery()

    total = await db.scalar(select(func.count()).select_from(union_subq)) or 0

    col_map = {
        "created_at": union_subq.c.created_at,
        "filename": union_subq.c.filename,
        "size_bytes": union_subq.c.size_bytes,
    }
    order_col = col_map.get(sort_by, union_subq.c.created_at)
    order_expr = order_col.desc() if sort_dir == "desc" else order_col.asc()

    rows = (
        await db.execute(
            select(union_subq)
            .order_by(order_expr)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()

    items: list[FileItem] = []
    for r in rows:
        # Build a safe download URL
        if r.category == "generated":
            # storage_path is like /data/images/<uuid>.png; extract the filename
            img_name = Path(r.storage_path).name if r.storage_path else str(r.file_id)
            url = f"/api/images/file/{img_name}"
        else:
            url = f"/api/files/download/{r.file_id}"

        # Build thumbnail URL if available
        thumb_url = None
        if r.thumbnail_path:
            # Extract filename from path like /data/pptx/<uuid>/thumbnail.png
            thumb_name = Path(r.thumbnail_path).name
            # We need a way to serve this. Since it's in the pptx dir, we can use a dedicated endpoint
            # or map it. For now, let's assume we add an endpoint /api/files/thumbnail/{file_id}
            thumb_url = f"/api/files/thumbnail/{r.file_id}"

        items.append(FileItem(
            id=r.file_id,
            filename=r.filename,
            mime_type=r.mime_type,
            kind=r.kind,
            size_bytes=r.size_bytes,
            source_type=r.source_type,
            conversation_id=r.conversation_id,
            message_id=r.message_id,
            created_at=r.created_at,
            download_url=url,
            category=r.category,
            thumbnail_url=thumb_url,
        ))

    return FileListResponse(items=items, total=total, page=page, per_page=per_page)


@router.get("/thumbnail/{file_id}")
async def get_thumbnail(
    file_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve a thumbnail image for a file (e.g., PPTX first slide)."""
    record = await db.get(Attachment, file_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    if not record.thumbnail_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No thumbnail available")

    path = Path(record.thumbnail_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thumbnail no longer exists on disk")

    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(path),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/download/{file_id}")
async def download_file(
    file_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve an uploaded attachment file with ownership check."""
    record = await db.get(Attachment, file_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    path = Path(record.storage_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File no longer exists on disk")

    # `original_filename` is a human-readable *label* (e.g. "PDF: convert to
    # pdf") used elsewhere in the UI. Using it as the download filename saves
    # the file with no extension and a colon in the name -- Windows won't
    # open the result. `filename` is the sanitised, extension-carrying name
    # (e.g. "PDF_Conversion_Request.pdf") and is what the browser must see.
    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(path),
        media_type=record.mime_type,
        filename=record.filename,
    )


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete(
    file_ids: list[uuid.UUID],
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple uploaded attachments. Only deletes files owned by the user."""
    deleted = 0
    for fid in file_ids:
        record = await db.get(Attachment, fid)
        if record and record.user_id == user.id:
            path = Path(record.storage_path)
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            await db.delete(record)
            deleted += 1
    await db.commit()
    return {"deleted": deleted}


# The literal "/all" route MUST be registered before the parameterised
# "/{file_id}" route. FastAPI matches in registration order, so if the
# parameterised route comes first, "all" gets fed to it as a UUID and the
# request fails with a 422 ("invalid uuid") that the client shows as a
# generic "Failed to delete files" -- exactly the bug we just fixed.
@router.delete("/all", status_code=status.HTTP_200_OK)
async def delete_all_files(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete ALL uploaded attachments for the current user."""
    records = (
        await db.execute(select(Attachment).where(Attachment.user_id == user.id))
    ).scalars().all()

    count = 0
    for record in records:
        path = Path(record.storage_path)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        await db.delete(record)
        count += 1

    await db.commit()
    return {"deleted": count}


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single uploaded attachment (ownership enforced)."""
    record = await db.get(Attachment, file_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    # Remove physical file
    path = Path(record.storage_path)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass  # best-effort; DB row removal is authoritative

    await db.delete(record)
    await db.commit()


# ── Admin endpoints ──────────────────────────────────────────────────────


@router.get("/admin/users/{target_user_id}/files", response_model=FileListResponse)
async def admin_list_user_files(
    target_user_id: uuid.UUID,
    _admin: User = Depends(require(Permission.USERS_READ)),
    db: AsyncSession = Depends(get_db),
    kind: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """Admin: list files for any user."""
    attach_q = select(
        Attachment.id.label("file_id"),
        Attachment.filename,
        Attachment.mime_type,
        Attachment.kind,
        Attachment.size_bytes,
        Attachment.storage_path,
        Attachment.source_type,
        Attachment.conversation_id,
        Attachment.message_id,
        Attachment.created_at,
        Attachment.thumbnail_path,
        sa.literal("uploaded").label("category"),
    ).where(Attachment.user_id == target_user_id)

    if kind:
        attach_q = attach_q.where(Attachment.kind == kind)
    if search:
        attach_q = attach_q.where(Attachment.filename.ilike(f"%{search}%"))

    gen_q = select(
        GeneratedImage.id.label("file_id"),
        GeneratedImage.prompt.label("filename"),
        sa.literal("image/png").label("mime_type"),
        sa.literal("image").label("kind"),
        sa.cast(0, sa.Integer).label("size_bytes"),
        GeneratedImage.file_path.label("storage_path"),
        sa.literal("generated").label("source_type"),
        sa.cast(None, sa.Uuid).label("conversation_id"),
        sa.cast(None, sa.Uuid).label("message_id"),
        GeneratedImage.created_at,
        sa.cast(None, sa.String).label("thumbnail_path"),
        sa.literal("generated").label("category"),
    ).where(
        GeneratedImage.user_id == target_user_id,
        ~GeneratedImage.file_path.in_(
            select(Attachment.storage_path).where(
                Attachment.user_id == target_user_id,
                Attachment.source_type == "generated",
            )
        ),
    )

    if kind and kind != "image":
        gen_q = gen_q.where(sa.false())
    if search:
        gen_q = gen_q.where(GeneratedImage.prompt.ilike(f"%{search}%"))

    union_subq = attach_q.union_all(gen_q).subquery()
    total = await db.scalar(select(func.count()).select_from(union_subq)) or 0

    rows = (
        await db.execute(
            select(union_subq)
            .order_by(union_subq.c.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()

    items: list[FileItem] = []
    for r in rows:
        if r.category == "generated":
            img_name = Path(r.storage_path).name if r.storage_path else str(r.file_id)
            url = f"/api/images/file/{img_name}"
        else:
            url = f"/api/files/download/{r.file_id}"
        thumb_url = None
        if r.thumbnail_path:
            thumb_url = f"/api/files/thumbnail/{r.file_id}"

        items.append(FileItem(
            id=r.file_id, filename=r.filename, mime_type=r.mime_type,
            kind=r.kind, size_bytes=r.size_bytes, source_type=r.source_type,
            conversation_id=r.conversation_id, message_id=r.message_id,
            created_at=r.created_at, download_url=url, category=r.category,
            thumbnail_url=thumb_url,
        ))

    return FileListResponse(items=items, total=total, page=page, per_page=per_page)


@router.delete("/admin/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_file(
    file_id: uuid.UUID,
    _admin: User = Depends(require(Permission.USERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    """Admin: delete any user's attachment by ID."""
    record = await db.get(Attachment, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    path = Path(record.storage_path)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass

    await db.delete(record)
    await db.commit()
