import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import current_user, require
from app.models import GeneratedImage, User
from app.rbac import Permission, has_permission
from app.schemas import ImageOut, ImageRequest
from app.services import imaging
from app.services.credits import assert_has_balance, record_usage
from app.services.errors import safe_message
from app.services.pricing import (
    IMAGE_PRICES,
    IMAGE_TOKEN_PRICES,
    image_cost_cents,
    image_cost_from_usage,
)

router = APIRouter(prefix="/api/images", tags=["images"])

IMAGE_DIR = Path("/data/images")
VALID_SIZES = {"1024x1024", "1024x1536", "1536x1024"}
VALID_QUALITIES = {"low", "medium", "high"}

# Filenames are generated as "<uuid4>.png"; nothing else is ever valid.
_SAFE_NAME = re.compile(r"[0-9a-fA-F-]{36}\.png")


def _url_for(path: str) -> str:
    return f"/api/images/file/{Path(path).name}"


@router.post("", response_model=ImageOut)
async def generate_image(
    payload: ImageRequest,
    user: User = Depends(require(Permission.AI_IMAGE)),
    db: AsyncSession = Depends(get_db),
):
    if payload.size not in VALID_SIZES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"size must be one of {VALID_SIZES}")
    if payload.quality not in VALID_QUALITIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"quality must be one of {VALID_QUALITIES}"
        )

    # Only the currently-offered family may be requested; historic entries in
    # IMAGE_PRICES stay only so old usage rows still bill correctly.
    model = payload.model if payload.model in IMAGE_TOKEN_PRICES else settings.image_model
    cost = image_cost_cents(model, payload.quality, payload.size)

    account = await assert_has_balance(db, user.id)
    if account.monthly_allocation_cents and account.remaining_cents < cost:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"This image costs {cost}c but only {account.remaining_cents}c remain this month.",
        )

    try:
        file_path, usage = await imaging.generate(
            payload.prompt, size=payload.size, quality=payload.quality, model=model
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, safe_message(exc, action="Image generation")
        )

    actual = image_cost_from_usage(model, usage)
    if actual is not None:
        cost = actual

    record = GeneratedImage(
        user_id=user.id,
        prompt=payload.prompt,
        model=model,
        size=payload.size,
        quality=payload.quality,
        file_path=str(file_path),
        cost_cents=cost,
    )
    db.add(record)
    await db.flush()
    await record_usage(
        db, user_id=user.id, kind="image", model=model, cost_cents=cost, images=1
    )

    return ImageOut(
        id=record.id,
        prompt=record.prompt,
        url=_url_for(record.file_path),
        size=record.size,
        quality=record.quality,
        cost_cents=record.cost_cents,
        created_at=record.created_at,
    )


@router.get("/file/{filename}")
async def serve_image(
    filename: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve a generated image to users entitled to see it.

    These were previously exposed by a StaticFiles mount, which meant any
    picture -- including student work -- was readable by anyone who had the
    URL, forever, with no login. Unguessable filenames are obscurity, not
    access control: URLs leak through shared links, history and proxy logs.
    """
    # Reject anything that is not a bare UUID-style filename before it reaches
    # the filesystem, so no input can walk out of the media directory.
    if not _SAFE_NAME.fullmatch(filename):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")

    path = (IMAGE_DIR / filename).resolve()
    if path.parent != IMAGE_DIR.resolve() or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")

    owns = await db.scalar(
        select(GeneratedImage.id)
        .where(
            GeneratedImage.user_id == user.id,
            GeneratedImage.file_path.like(f"%{filename}"),
        )
        .limit(1)
    )
    # Staff who can already read usage reports can also see what was produced;
    # everyone else sees only their own images.
    if owns is None and not has_permission(user.role, Permission.REPORTS_READ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")

    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("", response_model=list[ImageOut])
async def my_gallery(
    limit: int = 50,
    user: User = Depends(require(Permission.AI_IMAGE)),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(GeneratedImage)
        .where(GeneratedImage.user_id == user.id)
        .order_by(desc(GeneratedImage.created_at))
        .limit(min(limit, 200))
    )
    return [
        ImageOut(
            id=r.id,
            prompt=r.prompt,
            url=_url_for(r.file_path),
            size=r.size,
            quality=r.quality,
            cost_cents=r.cost_cents,
            created_at=r.created_at,
        )
        for r in rows
    ]
