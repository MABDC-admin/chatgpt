"""Redis-based rate limiting.

Sliding window per user: each request increments a counter keyed by
user_id + current minute. When the count exceeds the limit the request
is rejected with 429. Separate limits for chat and image endpoints.
"""

import logging
import time

import redis.asyncio as redis

from app.config import settings

log = logging.getLogger("uvicorn.error")

_pool: redis.Redis | None = None

CHAT_LIMIT = 30  # requests per minute
IMAGE_LIMIT = 10
UPLOAD_LIMIT = 20
TOOLS_LIMIT = 15
KNOWLEDGE_LIMIT = 10
# Per source IP, not per account: credential stuffing sprays many usernames
# from one host, so a per-account counter would never trip.
LOGIN_LIMIT = 10


async def get_redis() -> redis.Redis:
    global _pool
    if _pool is None:
        # Bounded timeouts: without them a hung Redis would stall every guarded
        # request for the default socket timeout instead of failing open fast.
        _pool = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _pool


async def check_rate_limit(user_id: str, kind: str = "chat") -> tuple[bool, int]:
    """Returns (allowed, remaining). Does NOT raise; the caller decides.

    Fails open. Rate limiting protects the platform from abuse, but it is not
    required for a request to be *correct*, so an unreachable Redis must not
    take chat, tools and uploads down with it. A refused connection previously
    propagated as an unhandled 500 from every guarded endpoint.
    """
    limits = {
        "chat": CHAT_LIMIT,
        "image": IMAGE_LIMIT,
        "upload": UPLOAD_LIMIT,
        "tools": TOOLS_LIMIT,
        "knowledge": KNOWLEDGE_LIMIT,
        "login": LOGIN_LIMIT,
    }
    limit = limits.get(kind, CHAT_LIMIT)
    window = int(time.time()) // 60
    key = f"ratelimit:{kind}:{user_id}:{window}"

    try:
        r = await get_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)
        count = (await pipe.execute())[0]
    except Exception:
        log.warning("Rate limit check failed for %s; allowing request", kind, exc_info=False)
        return True, limit

    return count <= limit, max(limit - count, 0)


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
