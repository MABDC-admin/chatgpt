import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require, write_audit
from app.models import Attachment, AuditLog, CreditAccount, GeneratedImage, UsageLog, User
from app.rbac import Permission, Role, permissions_for
from app.schemas import CreditAllocationUpdate, UsageRow, UserCreate, UserOut, UserUpdate
from app.security import hash_password
from app.services.credits import get_or_create_account
from app.services.email_service import send_approval_email

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Only these roles may hand out elevated access.
_ELEVATED = {Role.SUPER_ADMIN, Role.ADMIN, Role.IT_ADMIN}


def _assert_can_assign(actor: User, target_role: str) -> None:
    try:
        role = Role(target_role)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown role {target_role}")
    if role in _ELEVATED and Role(actor.role) is not Role.SUPER_ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only a SUPER_ADMIN may assign administrator roles"
        )


def _to_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.permissions = sorted(p.value for p in permissions_for(user.role))
    return out


@router.get("/roles")
async def list_roles(_: User = Depends(require(Permission.USERS_READ))):
    return [
        {"role": r.value, "permissions": sorted(p.value for p in permissions_for(r.value))}
        for r in Role
    ]


@router.get("/users")
async def list_users(
    _: User = Depends(require(Permission.USERS_READ)), db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(User, CreditAccount.monthly_allocation_cents, CreditAccount.used_cents)
        .outerjoin(CreditAccount, CreditAccount.user_id == User.id)
        .order_by(User.full_name)
    )
    rows = (await db.execute(stmt)).all()
    result = []
    for user, alloc, used in rows:
        out = _to_out(user)
        out.monthly_allocation_cents = alloc or 0
        out.used_cents = used or 0
        result.append(out.model_dump())
    return result


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    actor: User = Depends(require(Permission.USERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    _assert_can_assign(actor, payload.role)
    email = payload.email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email already exists")

    user = User(
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.flush()

    account = await get_or_create_account(db, user.id)
    if payload.monthly_allocation_cents is not None:
        account.monthly_allocation_cents = payload.monthly_allocation_cents

    await write_audit(
        db,
        actor_id=actor.id,
        action="user.create",
        target=email,
        detail={"role": payload.role},
        request=request,
    )
    await db.commit()
    await db.refresh(user)
    return _to_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    actor: User = Depends(require(Permission.USERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    changed: dict[str, object] = {}
    if payload.role is not None and payload.role != user.role:
        _assert_can_assign(actor, payload.role)
        user.role = payload.role
        changed["role"] = payload.role
    if payload.full_name is not None:
        user.full_name = payload.full_name
        changed["full_name"] = payload.full_name
    if payload.is_active is not None:
        if user.id == actor.id and not payload.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot disable your own account")
        user.is_active = payload.is_active
        changed["is_active"] = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
        changed["password"] = "reset"

    await write_audit(
        db,
        actor_id=actor.id,
        action="user.update",
        target=user.email,
        detail=changed,
        request=request,
    )
    await db.commit()
    await db.refresh(user)
    return _to_out(user)


@router.put("/users/{user_id}/credits")
async def set_allocation(
    user_id: uuid.UUID,
    payload: CreditAllocationUpdate,
    request: Request,
    actor: User = Depends(require(Permission.CREDITS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    account = await get_or_create_account(db, user_id)
    account.monthly_allocation_cents = payload.monthly_allocation_cents
    await write_audit(
        db,
        actor_id=actor.id,
        action="credits.allocate",
        target=user.email,
        detail={"monthly_allocation_cents": payload.monthly_allocation_cents},
        request=request,
    )
    await db.commit()
    return {
        "user_id": str(user_id),
        "monthly_allocation_cents": account.monthly_allocation_cents,
        "used_cents": account.used_cents,
        "remaining_cents": account.remaining_cents,
    }


@router.get("/usage", response_model=list[UsageRow])
async def usage_report(
    days: int = 30,
    _: User = Depends(require(Permission.REPORTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=max(min(days, 365), 1))
    stmt = (
        select(
            User.id,
            User.full_name,
            User.email,
            User.role,
            func.count(UsageLog.id).filter(UsageLog.kind == "text").label("text_calls"),
            func.count(UsageLog.id).filter(UsageLog.kind == "image").label("image_calls"),
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(UsageLog.cost_cents), 0).label("cost_cents"),
            func.coalesce(func.max(CreditAccount.monthly_allocation_cents), 0).label("allocation"),
        )
        .select_from(User)
        .outerjoin(UsageLog, (UsageLog.user_id == User.id) & (UsageLog.created_at >= since))
        .outerjoin(CreditAccount, CreditAccount.user_id == User.id)
        .group_by(User.id, User.full_name, User.email, User.role)
        .order_by(desc("cost_cents"))
    )
    rows = (await db.execute(stmt)).all()
    return [
        UsageRow(
            user_id=r[0],
            full_name=r[1],
            email=r[2],
            role=r[3],
            text_calls=r[4],
            image_calls=r[5],
            prompt_tokens=r[6],
            completion_tokens=r[7],
            cost_cents=r[8],
            allocation_cents=r[9],
        )
        for r in rows
    ]


@router.get("/usage/summary")
async def usage_summary(
    days: int = 30,
    _: User = Depends(require(Permission.REPORTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=max(min(days, 365), 1))
    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageLog.cost_cents), 0),
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
                func.coalesce(func.sum(UsageLog.images), 0),
                func.count(func.distinct(UsageLog.user_id)),
            ).where(UsageLog.created_at >= since)
        )
    ).one()
    by_day = (
        await db.execute(
            select(
                func.date_trunc("day", UsageLog.created_at).label("day"),
                func.coalesce(func.sum(UsageLog.cost_cents), 0),
            )
            .where(UsageLog.created_at >= since)
            .group_by("day")
            .order_by("day")
        )
    ).all()
    return {
        "days": days,
        "cost_cents": totals[0],
        "prompt_tokens": totals[1],
        "completion_tokens": totals[2],
        "images": totals[3],
        "active_users": totals[4],
        "by_day": [{"day": d.isoformat(), "cost_cents": c} for d, c in by_day],
    }


@router.get("/audit")
async def audit_trail(
    limit: int = 100,
    _: User = Depends(require(Permission.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(AuditLog).order_by(desc(AuditLog.created_at)).limit(min(limit, 500))
    )
    return [
        {
            "id": r.id,
            "actor_id": str(r.actor_id) if r.actor_id else None,
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
            "ip": r.ip,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


IMAGE_DIR = Path("/data/images")
UPLOAD_DIR = Path("/data/uploads")


@router.get("/users/{user_id}/media")
async def user_media(
    user_id: uuid.UUID,
    _: User = Depends(require(Permission.REPORTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    """Return all generated images and uploaded attachments for a given user."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    images = (
        await db.scalars(
            select(GeneratedImage)
            .where(GeneratedImage.user_id == user_id)
            .order_by(desc(GeneratedImage.created_at))
            .limit(200)
        )
    ).all()

    attachments = (
        await db.scalars(
            select(Attachment)
            .where(Attachment.user_id == user_id)
            .order_by(desc(Attachment.created_at))
            .limit(200)
        )
    ).all()

    return {
        "images": [
            {
                "id": str(img.id),
                "prompt": img.prompt,
                "url": f"/api/images/file/{Path(img.file_path).name}",
                "size": img.size,
                "quality": img.quality,
                "cost_cents": img.cost_cents,
                "created_at": img.created_at.isoformat(),
                "type": "generated",
            }
            for img in images
        ],
        "attachments": [
            {
                "id": str(att.id),
                "filename": att.filename,
                "kind": att.kind,
                "mime_type": att.mime_type,
                "size_bytes": att.size_bytes,
                "url": f"/api/admin/files/attachments/{att.id}",
                "created_at": att.created_at.isoformat(),
                "type": "uploaded",
            }
            for att in attachments
        ],
    }


import re

_SAFE_NAME = re.compile(r"[0-9a-fA-F-]{36}\.png")


@router.get("/files/attachments/{attachment_id}")
async def serve_admin_attachment(
    attachment_id: uuid.UUID,
    _: User = Depends(require(Permission.REPORTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    """Serve an uploaded attachment file to admins only."""
    record = await db.get(Attachment, attachment_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")

    path = Path(record.storage_path).resolve()
    # Ensure the resolved path is within the uploads directory
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found on disk")

    return FileResponse(
        path,
        media_type=record.mime_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": f'inline; filename="{record.filename}"',
        },
    )


@router.patch("/users/{user_id}/approve")
async def approve_user(
    user_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require(Permission.USERS_WRITE)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"User is already {user.status}")

    user.status = "active"
    user.is_active = True
    await write_audit(
        db,
        actor_id=actor.id,
        action="user.approve",
        target=user.email,
        request=request,
    )
    await db.commit()

    # Send approval email (fire-and-forget; failures are logged internally)
    await send_approval_email(user.full_name, user.email)

    return {"message": f"User {user.email} approved and notified."}
