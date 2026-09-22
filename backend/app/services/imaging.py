"""Image generation, editing and storage.

Shared by the dedicated /api/images endpoints and by the unified chat flow,
so a picture made in either place lands in the same store and ledger.
"""

import base64
import uuid
from pathlib import Path

from app.config import settings
from app.services import ai

IMAGE_DIR = Path("/data/images")
VALID_SIZES = {"1024x1024", "1024x1536", "1536x1024"}
VALID_QUALITIES = {"low", "medium", "high"}


def media_url(path: str | Path) -> str:
    """URL stored on the message row.

    Points at the authenticated route rather than a static mount: these images
    are school work and must not be world-readable to anyone holding the link.
    """
    return f"/api/images/file/{Path(path).name}"


def path_from_url(url: str) -> Path:
    """Map a stored image URL back onto disk.

    Only the basename is used, so a stored value cannot reach outside the
    media directory even if it were tampered with.
    """
    return IMAGE_DIR / Path(url).name


def save_png(b64: str) -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = IMAGE_DIR / f"{uuid.uuid4()}.png"
    target.write_bytes(base64.b64decode(b64))
    return target


def _supports_response_format(model: str) -> bool:
    """gpt-image-2.5-* and qwen-image-* models reject the `response_format` parameter.

    The OpenAI SDK v1.x auto-injects `response_format="b64_json"` as a default
    for `images.generate()` and `images.edit()`. Newer image models like
    gpt-image-2.5-flare and qwen-image-3.0-pro return 400 BadRequestError when
    they see it. We must explicitly omit the parameter for those models while
    keeping it for older ones (gpt-image-1, gpt-image-1-mini) that require it.
    """
    if model.startswith("gpt-image-2.5"):
        return False
    if model.startswith("qwen-image"):
        return False
    return True


async def generate(
    prompt: str, *, size: str, quality: str, model: str
) -> tuple[Path, object]:
    """Generate an image. Returns (file, usage) so the caller can bill from
    the token counts the provider actually charged, not an estimate.

    Dispatches by model prefix: Qwen models go through Nexum Router (a
    separate OpenAI-*compatible* proxy that serves chat completions with a
    Markdown image URL rather than base64 payloads), everything else uses the
    OpenAI SDK client directly.
    """
    from app.services import qwen_client
    if qwen_client.is_qwen_model(model):
        return await qwen_client.generate(prompt, size=size, quality=quality, model=model)

    kwargs: dict = dict(model=model, prompt=prompt, size=size, quality=quality, n=1)
    if _supports_response_format(model):
        kwargs["response_format"] = "b64_json"
    result = await ai.client.images.generate(**kwargs)
    b64 = result.data[0].b64_json
    if not b64:
        raise RuntimeError("Provider returned no image data")
    return save_png(b64), getattr(result, "usage", None)


async def edit(
    sources: list[Path] | Path,
    instruction: str,
    *,
    size: str,
    quality: str,
    model: str,
) -> tuple[Path, object]:
    """Revise or compose images.

    Accepts one or more source images.  Multiple images let the model combine
    elements from each reference (e.g. "use the layout from the first picture
    and the logo from the second").  A single Path is still accepted for
    backwards compatibility.
    """
    if isinstance(sources, Path):
        sources = [sources]

    missing = [s for s in sources if not s.exists()]
    if missing:
        raise FileNotFoundError(f"Source image(s) missing: {missing}")

    # Qwen through Nexum: the source images are sent inline via base64 data
    # URLs in a chat completion, not through the OpenAI edit endpoint.
    from app.services import qwen_client
    if qwen_client.is_qwen_model(model):
        return await qwen_client.edit(
            sources, instruction, size=size, quality=quality, model=model,
        )

    edit_kwargs: dict = dict(
        model=model,
        prompt=instruction,
        size=size,
        quality=quality,
        n=1,
    )
    # gpt-image-2.5-* models reject both `response_format` and `input_fidelity`.
    # Older models (gpt-image-1) support and benefit from both.
    if _supports_response_format(model):
        edit_kwargs["response_format"] = "b64_json"
        # Keeps faces, text and layout of the source intact so a revision
        # changes only what was asked for, rather than redrawing the whole
        # picture from the instruction alone.
        edit_kwargs["input_fidelity"] = "high"

    handles = [s.open("rb") for s in sources]
    try:
        edit_kwargs["image"] = handles
        result = await ai.client.images.edit(**edit_kwargs)
    finally:
        for h in handles:
            h.close()

    b64 = result.data[0].b64_json
    if not b64:
        raise RuntimeError("Provider returned no image data")
    return save_png(b64), getattr(result, "usage", None)


def normalise(size: str | None, quality: str | None) -> tuple[str, str]:
    return (
        size if size in VALID_SIZES else "1536x1024",
        quality if quality in VALID_QUALITIES else "medium",
    )


def default_model(requested: str | None = None) -> str:
    """Pick the model for a new generation.

    Only the currently-offered models are honoured for *new* work; the older
    gpt-image-1 family is retired from selection but still appears in
    IMAGE_PRICES so historic usage rows keep billing correctly in reports.
    """
    from app.services import qwen_client
    from app.services.pricing import IMAGE_TOKEN_PRICES

    if requested in IMAGE_TOKEN_PRICES:
        return requested
    if requested and qwen_client.is_qwen_model(requested) and qwen_client.is_available():
        return requested
    return settings.image_model
