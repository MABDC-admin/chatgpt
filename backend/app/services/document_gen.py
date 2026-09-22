"""Document artifact generation for chat-triggered DOCX / PDF / XLSX turns.

Three code paths share the same shape:

    plan (LLM writes Markdown or JSON) -> build (deterministic renderer) -> save

The renderers already exist in `export.py`; this module owns the *planning*
step, so the router (chat.py) only calls one function per artifact type.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.config import settings
from app.services import ai, export

log = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_DOCX_PROMPT = """You are a school-document writer. Produce a well-structured Markdown document that answers the user's request. Use headings (# / ## / ###), short paragraphs, bullet or numbered lists, and Markdown tables where they add value. If the user attached a scan or worksheet image, transcribe and clean it up faithfully unless they asked otherwise.

Output ONLY Markdown -- no code fences around the whole document, no commentary.
Open with a single H1 title. Keep it printable: clear headings, tidy spacing."""

_XLSX_PROMPT = """You produce spreadsheet content for a school assistant. Return ONE valid JSON object of this shape and nothing else:

{
  "title": "short title, becomes the filename",
  "sheets": [
    {
      "name": "Sheet 1",
      "headers": ["Column A", "Column B", "Column C"],
      "rows": [
        ["value", "value", "value"],
        ["value", "value", "value"]
      ]
    }
  ]
}

Rules:
- Sheet names must be at most 31 characters and cannot contain / \\ ? * [ ] :.
- Rows must be aligned to the header count. Use "" for empty cells, not null.
- Numbers must be JSON numbers (no quotes). Percentages as fractions of 1.
- If the user uploaded a table image, transcribe it faithfully into rows.
- Prefer several small sheets to one giant sheet (e.g. "Roster", "Scores").

Do NOT wrap the JSON in a Markdown fence. Do NOT include commentary."""


# ---------------------------------------------------------------------------
# Planners
# ---------------------------------------------------------------------------

async def _plan_markdown(user_message: str, user_content) -> tuple[str, str]:
    """Ask the model to write the document as Markdown. Returns (title, body)."""
    completion = await ai.client.chat.completions.create(
        model=settings.text_model,
        messages=[
            {"role": "system", "content": _DOCX_PROMPT},
            {"role": "user", "content": user_content},
        ],
        **ai.completion_kwargs(settings.text_model, max_output=8000),
    )
    markdown = (completion.choices[0].message.content or "").strip()

    # Strip any accidental outer fence.
    markdown = re.sub(r"^```(?:markdown|md)?\s*\n", "", markdown)
    markdown = re.sub(r"\n```\s*$", "", markdown).strip()

    # Extract the first H1 as the document title; fall back to the user's request.
    title = "Document"
    for line in markdown.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()[:80]
            break
    else:
        title = user_message.strip()[:80] or "Document"

    return title, markdown


async def _plan_workbook(user_message: str, user_content) -> tuple[str, list[dict]]:
    """Ask the model to describe a workbook as JSON. Returns (title, sheets)."""
    completion = await ai.client.chat.completions.create(
        model=settings.text_model,
        messages=[
            {"role": "system", "content": _XLSX_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        **ai.completion_kwargs(settings.text_model, max_output=8000),
    )
    raw = (completion.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n", "", raw)
        raw = re.sub(r"\n```\s*$", "", raw).strip()

    data = json.loads(raw)
    title = str(data.get("title") or user_message.strip()[:80] or "Spreadsheet")
    sheets = list(data.get("sheets") or [])
    if not sheets:
        raise ValueError("Model returned no sheets")
    return title, sheets


# ---------------------------------------------------------------------------
# Public builders — one per artifact kind
# ---------------------------------------------------------------------------

async def generate_docx(user_message: str, user_content) -> tuple[bytes, str]:
    """Returns (bytes, filename)."""
    title, markdown = await _plan_markdown(user_message, user_content)
    data = export.to_docx(markdown, title)
    return data, export.safe_filename(title, "docx")


async def generate_pdf(user_message: str, user_content) -> tuple[bytes, str]:
    title, markdown = await _plan_markdown(user_message, user_content)
    data = export.to_pdf(markdown, title)
    return data, export.safe_filename(title, "pdf")


async def generate_xlsx(user_message: str, user_content) -> tuple[bytes, str]:
    title, sheets = await _plan_workbook(user_message, user_content)
    data = export.to_xlsx(sheets, title)
    return data, export.safe_filename(title, "xlsx")


ARTIFACT_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf":  "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# ---------------------------------------------------------------------------
# Image passthrough -- "combine these into a PDF" / "put these images in a Word doc"
# ---------------------------------------------------------------------------
#
# When the user attaches images and asks for a PDF or DOCX, the default flow
# runs the images through the LLM as vision input and produces a *description*
# of them as body text. That's the right behaviour for "type this scan out as
# a printable Word doc" but the wrong one for "combine these three photos
# into a PDF" -- there the user wants the actual pixels on the page, not a
# transcription.
#
# We split the two cases with a small keyword test on the message, plus the
# hard requirement that at least one attachment is an image. If both hold,
# the caller skips the LLM planner and calls one of the passthrough
# functions below, which write the images directly onto the artifact.

import re

_ARTIFACT_WORDS = r"(?:pdf|word(?:\s+doc(?:ument)?)?|docx|document|file)"

_COMBINE_KEYWORDS = re.compile(
    # Any of:
    #   "combine|merge|assemble|stitch ..."
    #   "put these into a PDF"
    #   "into a Word document from these"
    #   "make/create/build a Word document from these"
    r"\b(combine|merge|assemble|stitch|"
    r"put\s+(?:these|them|those)\s+in(?:to)?\s+(?:a\s+|one\s+)?" + _ARTIFACT_WORDS + r"|"
    r"in(?:to)?\s+(?:a\s+|one\s+)?" + _ARTIFACT_WORDS + r"\s+(?:from|of|with)\s+these|"
    r"(?:make|create|build)\s+(?:a\s+)?" + _ARTIFACT_WORDS + r"\s+(?:from|of|with)\s+these)\b",
    re.IGNORECASE,
)


def wants_image_passthrough(user_message: str, image_paths: list) -> bool:
    """Decide whether the artifact should embed images verbatim.

    True only when at least one image is attached AND the message contains
    an unambiguous "combine these images" phrasing. This is deliberately
    conservative -- a user who says "make me a worksheet based on this
    photo" wants transcription, not passthrough, even though a photo is
    present.
    """
    return bool(image_paths) and bool(_COMBINE_KEYWORDS.search(user_message or ""))


def images_to_pdf(image_paths: list, title: str) -> tuple[bytes, str]:
    """Rasterize each image onto its own A4 page, letterboxed to fit."""
    import io as _io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as _canvas
    from PIL import Image

    buf = _io.BytesIO()
    pdf = _canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    margin = 15 * mm
    max_w = page_w - 2 * margin
    max_h = page_h - 2 * margin

    for path in image_paths:
        try:
            with Image.open(path) as img:
                img = img.convert("RGB") if img.mode != "RGB" else img
                w, h = img.size
                # Letterbox scale, preserving aspect ratio.
                scale = min(max_w / w, max_h / h)
                draw_w = w * scale
                draw_h = h * scale
                x = (page_w - draw_w) / 2
                y = (page_h - draw_h) / 2
                # ReportLab drawImage streams the file from disk; that's fine
                # since our image_paths point at attachment storage_path values.
                pdf.drawImage(str(path), x, y, width=draw_w, height=draw_h,
                              preserveAspectRatio=True, mask="auto")
                pdf.showPage()
        except Exception as exc:
            log.warning("Skipped image %s in passthrough PDF: %s", path, exc)

    pdf.save()
    filename = _clean_filename(title, "pdf")
    return buf.getvalue(), filename


def images_to_docx(image_paths: list, title: str) -> tuple[bytes, str]:
    """One image per section, centered on the page at page-width."""
    import io as _io
    import docx
    from docx.shared import Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    document = docx.Document()
    document.add_heading(title, level=0)

    for path in image_paths:
        try:
            para = document.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run()
            # Page usable width on A4 (Word default) is roughly 6 inches.
            run.add_picture(str(path), width=Inches(6.0))
            document.add_paragraph()  # gap between images
        except Exception as exc:
            log.warning("Skipped image %s in passthrough DOCX: %s", path, exc)

    buf = _io.BytesIO()
    document.save(buf)
    filename = _clean_filename(title, "docx")
    return buf.getvalue(), filename


def _clean_filename(base: str, ext: str) -> str:
    """Local mirror of export.safe_filename so callers don't need to import it."""
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", (base or "combined").strip())
    return f"{(cleaned[:60] or 'Combined').replace(' ', '_')}.{ext}"
