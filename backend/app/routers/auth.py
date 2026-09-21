from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import client_ip, current_user, write_audit
from app.models import User
from app.rbac import permissions_for
from app.schemas import (
    CreditSummary,
    LoginRequest,
    PasswordChange,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.services.credits import get_or_create_account
from app.services.email_service import send_credit_request_email
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)):
    email = payload.email.lower()
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        # Don't reveal if the email exists; just say request submitted.
        return {"message": "If this email is not already registered, your request has been submitted for approval."}

    user = User(
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role="TEACHER", # Default role for signups
        is_active=False,
        status="pending",
    )
    db.add(user)
    await db.commit()
    return {"message": "Your account request has been submitted. You will receive an email once approved."}


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Throttle by source IP before touching the database. Without this the
    # endpoint accepts unlimited password guesses, and bcrypt verification is
    # deliberately slow, so it doubles as a cheap denial-of-service vector.
    allowed, _ = await check_rate_limit(client_ip(request), "login")
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-in attempts. Wait a minute and try again.",
        )

    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    # Same error for unknown email and bad password: do not leak which accounts exist.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    
    user_status = getattr(user, "status", None) or "active"
    if user_status == "pending":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is awaiting admin approval.")
    if not user.is_active or user_status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    user.last_login_at = datetime.now(timezone.utc)
    await write_audit(db, actor_id=user.id, action="auth.login", request=request)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id, user.role))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    out = UserOut.model_validate(user)
    out.permissions = sorted(p.value for p in permissions_for(user.role))
    return out


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    payload: PasswordChange,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change your own password.

    The current password is required even though the caller is already
    authenticated: it stops someone using a borrowed session to lock the real
    owner out of their account.
    """
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The new password must be different from the current one",
        )

    user.password_hash = hash_password(payload.new_password)
    await write_audit(db, actor_id=user.id, action="auth.password_change", request=request)
    await db.commit()


@router.get("/me/credits", response_model=CreditSummary)
async def my_credits(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    account = await get_or_create_account(db, user.id)
    await db.commit()
    return CreditSummary(
        monthly_allocation_cents=account.monthly_allocation_cents,
        used_cents=account.used_cents,
        remaining_cents=account.remaining_cents,
        period_start=account.period_start,
    )


@router.post("/me/request-credits")
async def request_more_credits(
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Notify admin that a teacher needs more credits.

    Rate-limited to one request per hour per user to prevent spam.
    Verifies the user actually has low/no credits before sending.
    """
    allowed, retry_after = await check_rate_limit(str(user.id), "credit_request")
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You already requested credits recently. Try again in {retry_after} seconds.",
        )

    account = await get_or_create_account(db, user.id)
    # Only allow requests when credits are genuinely exhausted or very low
    if account.monthly_allocation_cents > 0 and account.remaining_cents > 50:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You still have sufficient credits. No top-up needed right now.",
        )

    await send_credit_request_email(
        user_name=user.full_name,
        user_email=user.email,
        remaining_cents=account.remaining_cents,
    )

    await write_audit(
        db,
        actor_id=user.id,
        action="credits.request_topup",
        target=user.email,
        detail={"remaining_cents": account.remaining_cents},
        request=request,
    )
    await db.commit()

    return {"message": "Admin has been notified. They will review your request shortly."}
