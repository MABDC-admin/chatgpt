"""Nexum Router (Dialagram) client for Qwen image generation.

Nexum is OpenAI-*compatible* for text but its image endpoint is grafted onto
`/chat/completions`: the request looks like a chat call, and the response is a
chat completion whose content contains a Markdown image URL rather than a
base64 payload. So the OpenAI SDK's `images.generate()` cannot be used
verbatim -- we make the chat call, extract the URL, and download the bytes
into the same on-disk store the OpenAI path uses.
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger("uvicorn.error")

# ``imaging.IMAGE_DIR`` is the single source of truth for where PNGs live.
# Imported lazily inside functions to avoid a circular import.

_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")

_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


def is_available() -> bool:
    """True when a Nexum key has been configured on the server."""
    return bool(settings.nexum_api_key)


def is_qwen_model(model: str) -> bool:
    """Whether the given model should be routed via Nexum instead of OpenAI."""
    return bool(model and model.lower().startswith("qwen"))


class QwenError(RuntimeError):
    """Raised on any failure the caller should surface as a 502."""


async def _post(path: str, body: dict) -> dict:
    if not is_available():
        raise QwenError("Nexum is not configured on this server")

    url = f"{settings.nexum_base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.nexum_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if r.status_code >= 400:
        # Log the full body server-side; caller decides how much to expose.
        log.error("Nexum %s -> %s: %s", path, r.status_code, r.text[:500])
        raise QwenError(
            f"Nexum returned {r.status_code}. Ask an administrator to check the router configuration."
        )
    return r.json()


def _extract_image_url(chat_response: dict) -> str:
    """Pull the first https:// image URL out of a chat completion response."""
    try:
        content = chat_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise QwenError(f"Unexpected Nexum response shape: {exc}")

    match = _MARKDOWN_IMAGE.search(str(content or ""))
    if not match:
        raise QwenError("Nexum response contained no image URL")
    return match.group(1)


async def _download_png(image_url: str) -> bytes:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(image_url)
    if r.status_code != 200:
        raise QwenError(f"Downloading generated image failed with {r.status_code}")
    return r.content


def _save_png(data: bytes) -> Path:
    """Same location and filename shape as OpenAI-generated images, so the
    rest of the pipeline (URLs, galleries, deletes) works unchanged."""
    from app.services import imaging  # local import; imaging imports us back
    imaging.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = imaging.IMAGE_DIR / f"{uuid.uuid4()}.png"
    target.write_bytes(data)
    return target


# ---------------------------------------------------------------------------
# Public: same signature as imaging.generate/edit so the router can dispatch.
# ---------------------------------------------------------------------------

async def generate(
    prompt: str, *, size: str, quality: str, model: str
) -> tuple[Path, dict]:
    """Text-to-image via Nexum. Returns (file_path, usage_summary)."""
    body = {
        "model": _model_id(model),
        "messages": [{"role": "user", "content": _prompt_with_size(prompt, size)}],
    }
    response = await _post("/chat/completions", body)
    url = _extract_image_url(response)
    png = await _download_png(url)
    path = _save_png(png)
    return path, response.get("usage") or {}


async def edit(
    sources: list[Path] | Path,
    instruction: str,
    *,
    size: str,
    quality: str,
    model: str,
) -> tuple[Path, dict]:
    """Revise an existing image via Nexum. Sends the source picture as an
    inline base64 data URL alongside the instruction, so Qwen can see what
    it is being asked to change."""
    if isinstance(sources, Path):
        sources = [sources]
    missing = [s for s in sources if not s.exists()]
    if missing:
        raise FileNotFoundError(f"Source image(s) missing: {missing}")

    content: list[dict] = [
        {"type": "text", "text": _prompt_with_size(instruction, size)},
    ]
    for src in sources:
        b64 = base64.b64encode(src.read_bytes()).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    body = {
        "model": _model_id(model),
        "messages": [{"role": "user", "content": content}],
    }
    response = await _post("/chat/completions", body)
    url = _extract_image_url(response)
    png = await _download_png(url)
    path = _save_png(png)
    return path, response.get("usage") or {}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _model_id(requested: str) -> str:
    """Nexum publishes `qwen-image` today; the picker asks for it under that
    id already, but if a caller passes `qwen-image-3.0-pro` or any other
    aspirational tier we still hit the one that actually exists."""
    return "qwen-image"


def _prompt_with_size(prompt: str, size: str) -> str:
    """Prepend a strong directive about output dimensions so Qwen models
    respect the requested orientation even when the native size parameter
    isn't honoured by the router."""
    hint = {
        "1024x1024": "Generate a SQUARE image (1:1 aspect ratio).",
        "1024x1536": "Generate a PORTRAIT image (2:3 aspect ratio, taller than wide).",
        "1536x1024": "Generate a LANDSCAPE image (3:2 aspect ratio, wider than tall).",
    }.get(size, "")
    if not hint:
        return prompt
    return f"{hint}\n\n{prompt.strip()}"
