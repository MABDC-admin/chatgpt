import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import User
from app.rbac import Role
from app.routers import admin, auth, chat, config, files, images, knowledge, tools, uploads
from app.security import hash_password
from app.services.credits import get_or_create_account

log = logging.getLogger("uvicorn.error")


def _run_migrations() -> None:
    """Apply Alembic migrations.

    Runs in a worker thread: Alembic drives its own event loop internally and
    would otherwise collide with the running one.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    command.upgrade(config, "head")


async def init_database() -> None:
    async with engine.begin() as conn:
        # pgvector must exist before the document_chunks table is created.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # create_all handles table *creation* and is a no-op for tables that
        # already exist; Alembic owns *alterations* from here on. Migration
        # DDL is written idempotently so the two cannot conflict.
        await conn.run_sync(Base.metadata.create_all)

    await asyncio.to_thread(_run_migrations)


async def seed_admin() -> None:
    """Create the bootstrap SUPER_ADMIN on an empty database, once."""
    async with SessionLocal() as db:
        if await db.scalar(select(User).limit(1)):
            return
        user = User(
            email=settings.seed_admin_email.lower(),
            full_name="Platform Administrator",
            password_hash=hash_password(settings.seed_admin_password),
            role=Role.SUPER_ADMIN.value,
        )
        db.add(user)
        await db.flush()
        account = await get_or_create_account(db, user.id)
        account.monthly_allocation_cents = 0  # unmetered
        await db.commit()
        log.warning(
            "Seeded bootstrap admin %s -- change this password immediately.", user.email
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    await seed_admin()
    yield
    from app.services.rate_limit import close_redis
    await close_redis()
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(images.router)
app.include_router(admin.router)
app.include_router(uploads.router)
app.include_router(files.router)
app.include_router(tools.router)
app.include_router(knowledge.router)
app.include_router(config.router)

Path("/data/images").mkdir(parents=True, exist_ok=True)
Path("/data/uploads").mkdir(parents=True, exist_ok=True)
Path("/data/knowledge").mkdir(parents=True, exist_ok=True)
# No StaticFiles mount for /data/images: it served every generated picture to
# anyone with the URL and no login. Images now go through the authenticated
# GET /api/images/file/{filename} route instead.


@app.get("/api/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
