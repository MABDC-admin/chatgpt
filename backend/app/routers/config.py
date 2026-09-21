"""Public config the frontend needs to gate its UI.

Anything on the client that could advertise a feature the server can't deliver
belongs here. The Qwen tile in the image-model picker is gated by whether
Nexum Router has been configured; keeping this decision on the server means
the UI is always honest about what will actually work.
"""

from fastapi import APIRouter

from app.services import qwen_client

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/image-providers")
async def image_providers() -> dict:
    """Which image providers are enabled on this server."""
    return {
        "openai": True,       # always: the platform requires an OpenAI key at boot
        "nexum_qwen": qwen_client.is_available(),
    }
