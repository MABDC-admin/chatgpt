import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CreditAccount, UsageLog


def _period_of(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


async def get_or_create_account(db: AsyncSession, user_id: uuid.UUID) -> CreditAccount:
    account = await db.scalar(select(CreditAccount).where(CreditAccount.user_id == user_id))
    if account is None:
        account = CreditAccount(
            user_id=user_id,
            monthly_allocation_cents=settings.default_monthly_allocation_cents,
            used_cents=0,
            period_start=_period_of(datetime.now(timezone.utc)),
        )
        db.add(account)
        await db.flush()
    return await roll_period(db, account)


async def roll_period(db: AsyncSession, account: CreditAccount) -> CreditAccount:
    """Reset usage when the calendar month turns over."""
    current = _period_of(datetime.now(timezone.utc))
    if _period_of(account.period_start) < current:
        account.used_cents = 0
        account.period_start = current
        await db.flush()
    return account


async def assert_has_balance(db: AsyncSession, user_id: uuid.UUID) -> CreditAccount:
    """Gate before a call. Allocation of 0 means unmetered."""
    account = await get_or_create_account(db, user_id)
    if account.monthly_allocation_cents == 0:
        return account
    if account.remaining_cents <= 0:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Monthly AI credit exhausted. Ask an administrator to raise your allocation.",
        )
    return account


async def record_usage(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    model: str,
    cost_cents: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    images: int = 0,
) -> None:
    """Write the ledger row and deduct, in one transaction with the caller."""
    await get_or_create_account(db, user_id)

    # Deduct with an atomic UPDATE rather than `account.used_cents += cost`.
    # The Python form is a read-modify-write against a value each session
    # cached separately (expire_on_commit=False), so concurrent turns all read
    # the same starting balance and the last writer wins. Measured: 20 parallel
    # 1-cent charges recorded 2 cents. Doing the arithmetic in the database
    # makes each increment serialise on the row lock instead.
    await db.execute(
        update(CreditAccount)
        .where(CreditAccount.user_id == user_id)
        .values(used_cents=CreditAccount.used_cents + cost_cents)
    )
    db.add(
        UsageLog(
            user_id=user_id,
            kind=kind,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            images=images,
            cost_cents=cost_cents,
        )
    )
    await db.commit()
