import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import AuditLog, User
from app.rbac import Permission, has_permission
from app.security import decode_access_token

bearer = HTTPBearer(auto_error=False)


async def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    payload = decode_access_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or disabled")
    return user


def require(permission: Permission) -> Callable:
    """Dependency factory: `Depends(require(Permission.AI_IMAGE))`."""

    async def _guard(user: User = Depends(current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role {user.role} lacks permission {permission.value}",
            )
        return user

    return _guard


def client_ip(request: Request) -> str:
    """Identify the caller for throttling and audit.

    Uses X-Real-IP, which the edge nginx *overwrites* with the true peer, and
    which the inner nginx passes through unchanged. Deliberately not
    X-Forwarded-For: nginx appends to that header, so its first entry is
    attacker-controlled, and uvicorn's proxy handling reads exactly that entry.
    Keying the login throttle on it allowed the limit to be bypassed by
    rotating the header on each attempt.
    """
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    return request.client.host if request.client else "unknown"


async def write_audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    target: str | None = None,
    detail: dict | None = None,
    request: Request | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target=target,
            detail=detail,
            ip=client_ip(request) if request else None,
        )
    )
