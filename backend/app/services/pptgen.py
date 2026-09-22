"""PowerPoint generation.

Rewritten 2026-09-21. The previous three-phase pipeline had the LLM emit
Python code that we executed inside a Docker sandbox; that produced
inconsistent decks (LLM-authored code with syntax errors, timeouts,
sandbox image drift, and different look-and-feel every run). This module
now runs a deterministic layout engine on the planner's JSON output, so
the same plan always produces the same deck.

Public surface -- kept intentionally identical to the previous version so
call sites (routers/chat.py, routers/tools.py) do not need to change:

    PPTX_DIR
    generate_slide_plan(user_message, history=None, attachments_text="")
    generate_slide_images(plan, img_model, img_quality, img_size, progress_cb=None)
    build_presentation(plan, image_paths, run_id) -> Path
    _generate_thumbnail(pptx_path) -> Path | None
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from app.config import settings
from app.services import ai, imaging

logger = logging.getLogger(__name__)

PPTX_DIR = Path("/data/pptx")

# ---------------------------------------------------------------------------
# Phase 1 -- plan
# ---------------------------------------------------------------------------

_PLAN_PROMPT = """\
You are TEACHERDECK, an expert lesson presentation planner for K-12 and higher education.

Given the user's request, produce a JSON array of slide objects. Each object has these fields:

- "slide_number": integer (1-based)
- "title": string (a descriptive, engaging slide title — never generic like "Definition" or "Slide 3")
- "layout": one of "title", "content", "two_column", "image", "quiz", "summary", "activity", "quote", "hook", "objectives", "exit_ticket"
- "bullets": array of short strings. Empty for image/quote/hook layouts. At most 5 items, at most 12 words each. Use simple student-friendly language.
- "speaker_notes": string. Expanded explanation for the teacher following this structure per major slide:
  • Explain: what the teacher should say
  • Example: a concrete example that makes the idea clearer
  • Ask: a question for learners
  • Expected Answer: a possible learner response
  • Misconception Check: something learners commonly misunderstand
  • Transition: a sentence connecting to the next slide
- "needs_image": boolean
- "image_prompt": string or null. When needs_image is true, write a detailed visual prompt that DIRECTLY ILLUSTRATES this specific slide's title and bullet content. The image must teach or reinforce the exact concept on this slide — never generic decoration. Describe what the viewer sees, how it connects to the lesson point, the art style, color palette matching the theme, and composition. Example: if the slide teaches "Water evaporates from oceans", the image_prompt should describe a diagram or scene showing sun heating ocean water with vapor rising — not just "a pretty ocean".
- "quote_text": string. Used only when layout == "quote"; a one-line quotation.
- "quote_author": string or empty. Attribution for the quote.

## TEACHERDECK METHODOLOGY
Before creating slides, mentally analyze:
1. What learners need to know, understand, and be able to do
2. Important vocabulary and prerequisite knowledge
3. Possible misconceptions
4. Real-life applications appropriate for the learner's level
5. Measurable learning objectives (knowledge, understanding, application, analysis)

Then follow this recommended lesson flow (adapt based on requested slide count):
- Slide 1: Title with engaging visual
- Slide 2: Hook (surprising question, mystery image, scenario, fun fact, or prediction)
- Slide 3: Learning Objectives (simple, measurable, realistic)
- Slide 4: Prior Knowledge Activation ("What do you already know?")
- Slides 5+: Main concepts (one concept per slide, explanation → example → guided practice)
- Include: Real-life connection, misconception check, interactive activity, critical thinking question
- Final slides: Quick quiz (3-5 questions, easy→challenging), lesson summary ("What did we learn?"), exit ticket

## DESIGN RULES
- ONE SLIDE = ONE MAIN IDEA. Never overcrowd.
- Use simple, age-appropriate language. Short sentences. Concrete examples.
- Vary layouts across the deck: mix title, content, two_column, image, hook, quiz, activity, summary.
- Every slide must have a teaching purpose. If it doesn't contribute to the learning objective, remove it.
- Put detailed explanations in speaker_notes, NOT on the slide itself.
- Include at least one interactive moment every 3-4 slides (Think-Pair-Share, True/False, Predict, Quick Challenge).
- Questions should range from Easy (recall) → Average (understanding) → Challenging (application/analysis).
- Build the lesson: Simple → Understandable → Applied → Challenging.
- Default to the number of slides the user requests. If unspecified, use 15 slides.

## INPUT FORMAT
The user may send a structured TEACHERDECK command:
```
TEACHERDECK:
Topic: [TOPIC]
Subject: [SUBJECT]
Level: [GRADE/LEVEL]
Duration: [TIME] minutes
Slides: [NUMBER]

Additional instructions: [EXTRA CONTEXT]
```
Parse these fields and tailor the presentation accordingly. Adjust language complexity, examples, and activities to match the specified grade level.

Output ONLY a valid JSON array. No markdown fences, no commentary.
"""


async def generate_slide_plan(
    user_message: str,
    history: list[dict] | None = None,
    attachments_text: str = "",
) -> list[dict]:
    """Ask the LLM to produce a structured slide plan (JSON array of slides)."""
    system = _PLAN_PROMPT
    if getattr(ai, "PPT_MASTER_PROMPT", None):
        system += "\n\n--- SMART POWERPOINT GUIDELINES ---\n" + ai.PPT_MASTER_PROMPT[:4000]

    user_parts: list[str] = []
    if attachments_text:
        user_parts.append(f"Source material:\n{attachments_text}\n")
    if history:
        ctx = "\n".join(
            f"{m['role']}: {m['content'][:200]}" for m in history[-6:]
            if isinstance(m.get("content"), str)
        )
        if ctx:
            user_parts.append(f"Recent conversation context:\n{ctx}\n")
    user_parts.append(f"Presentation request: {user_message}")

    completion = await ai.client.chat.completions.create(
        model=settings.text_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ],
        **ai.completion_kwargs(settings.text_model, max_output=8000),
    )
    raw = (completion.choices[0].message.content or "").strip()

    # Recover from a model that wraps its JSON in a fence regardless.
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            raise ValueError(f"LLM did not return valid JSON plan. Got: {raw[:300]}")
        plan = json.loads(match.group())

    if not isinstance(plan, list):
        raise ValueError(f"Plan must be a JSON array, got {type(plan).__name__}")
    return plan


# ---------------------------------------------------------------------------
# Phase 2 -- images
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str], Awaitable[None]]


async def generate_slide_images(
    plan: list[dict],
    img_model: str,
    img_quality: str,
    img_size: str,
    progress_cb: ProgressCallback | None = None,
) -> dict[int, Path]:
    """Generate images for slides that need them. Returns {slide_number: path}.

    A per-slide failure is logged and skipped rather than sinking the deck --
    a slide without its image still ships with the rest of the content.
    """
    slides_needing = [s for s in plan if s.get("needs_image") and s.get("image_prompt")]
    total = len(slides_needing)
    results: dict[int, Path] = {}

    for i, slide in enumerate(slides_needing):
        num = slide.get("slide_number", i + 1)
        prompt = slide["image_prompt"]
        if progress_cb:
            await progress_cb(i + 1, total, f"Generating image {i + 1} of {total}…")
        try:
            path, _usage = await imaging.generate(
                prompt, size=img_size, quality=img_quality, model=img_model
            )
            results[num] = path
        except Exception as exc:
            logger.error("Slide %d image failed: %s", num, exc)

    return results


# ---------------------------------------------------------------------------
# Phase 3 -- deterministic layout engine
# ---------------------------------------------------------------------------

# Slide dimensions are 16:9 widescreen (13.33in x 7.5in). Every layout below
# lays out coordinates in inches so python-pptx can convert to EMUs itself.
SLIDE_W = 13.33
SLIDE_H = 7.5


def _color(hex_str: str):
    from pptx.dml.color import RGBColor
    hex_str = hex_str.lstrip("#")
    return RGBColor(int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))


PALETTE = {
    "primary":    "059669",  # emerald
    "secondary":  "1e40af",  # deep blue
    "accent":     "f59e0b",  # amber
    "dark_text":  "1f2937",
    "light_text": "ffffff",
    "bg_white":   "ffffff",
    "bg_light":   "f8fafc",
    "bg_quiz":    "fef3c7",
    "bg_summary": "d1fae5",
    "dark_bg":    "0f172a",
}

# Visual themes teachers can pick from. Each theme is a full palette swap.
THEMES: dict[str, dict[str, str]] = {
    "emerald": PALETTE,  # default — the original classroom green
    "ocean": {
        "primary": "0369a1", "secondary": "1e3a5f", "accent": "38bdf8",
        "dark_text": "0c4a6e", "light_text": "ffffff",
        "bg_white": "f0f9ff", "bg_light": "e0f2fe", "bg_quiz": "bae6fd",
        "bg_summary": "dbeafe", "dark_bg": "082f49",
    },
    "sunset": {
        "primary": "c2410c", "secondary": "7c2d12", "accent": "fb923c",
        "dark_text": "431407", "light_text": "ffffff",
        "bg_white": "fff7ed", "bg_light": "ffedd5", "bg_quiz": "fed7aa",
        "bg_summary": "fecaca", "dark_bg": "1c1917",
    },
    "lavender": {
        "primary": "7c3aed", "secondary": "4c1d95", "accent": "c084fc",
        "dark_text": "2e1065", "light_text": "ffffff",
        "bg_white": "faf5ff", "bg_light": "f3e8ff", "bg_quiz": "e9d5ff",
        "bg_summary": "ddd6fe", "dark_bg": "1e1b4b",
    },
    "minimal": {
        "primary": "18181b", "secondary": "3f3f46", "accent": "a1a1aa",
        "dark_text": "09090b", "light_text": "ffffff",
        "bg_white": "ffffff", "bg_light": "f4f4f5", "bg_quiz": "e4e4e7",
        "bg_summary": "d4d4d8", "dark_bg": "09090b",
    },
}


def _set_bg(slide, hex_color: str) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = _color(hex_color)


def _add_rect(slide, left, top, width, height, hex_color: str, hex_line: str | None = None):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _color(hex_color)
    if hex_line:
        shape.line.color.rgb = _color(hex_line)
    else:
        shape.line.fill.background()
    return shape


def _add_rounded(slide, left, top, width, height, hex_color: str):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _color(hex_color)
    shape.line.fill.background()
    return shape


def _add_text(
    slide, left, top, width, height, text: str, *,
    size: int, bold: bool = False, hex_color: str = "1f2937",
    align: str = "left",
):
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(size)
    p.font.bold = bold
    p.font.name = "Calibri"
    p.font.color.rgb = _color(hex_color)
    p.alignment = {
        "left": PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
    }.get(align, PP_ALIGN.LEFT)
    return tf


def _add_bullets(slide, left, top, width, height, bullets: list[str], hex_color: str = "1f2937"):
    from pptx.util import Inches, Pt
    if not bullets:
        return None
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    for i, bullet in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = "•  " + str(bullet)
        p.font.size = Pt(22)
        p.font.name = "Calibri"
        p.font.color.rgb = _color(hex_color)
        p.space_after = Pt(10)
    return tf


def _add_notes(slide, text: str) -> None:
    if not text:
        return
    try:
        slide.notes_slide.notes_text_frame.text = text
    except Exception as exc:
        logger.warning("Could not attach speaker notes: %s", exc)


# ---- layout renderers -----------------------------------------------------
# Every renderer accepts `t` (theme dict) so colours swap per theme.

def _render_title(deck, spec: dict, t: dict) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])  # blank
    _set_bg(slide, t["dark_bg"])
    _add_rect(slide, 0, 0, SLIDE_W, 0.15, t["primary"])
    _add_rect(slide, 0, SLIDE_H - 0.15, SLIDE_W, 0.15, t["accent"])
    _add_text(
        slide, 1, 2.4, SLIDE_W - 2, 1.4,
        spec.get("title", "Presentation"),
        size=48, bold=True, hex_color=t["light_text"], align="center",
    )
    subtitle = ""
    if spec.get("bullets"):
        subtitle = spec["bullets"][0]
    _add_text(
        slide, 1, 4.0, SLIDE_W - 2, 0.8,
        subtitle, size=22, hex_color="94a3b8", align="center",
    )
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_hook(deck, spec: dict, t: dict) -> None:
    """Attention-grabbing opening: large question or scenario on dark background."""
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["dark_bg"])
    _add_rect(slide, 0, 0, 0.25, SLIDE_H, t["accent"])
    _add_text(
        slide, 1.2, 1.8, SLIDE_W - 2.4, 2.0,
        spec.get("title", "Think about this…"),
        size=40, bold=True, hex_color=t["light_text"], align="center",
    )
    if spec.get("bullets"):
        _add_text(
            slide, 1.5, 4.2, SLIDE_W - 3, 1.5,
            spec["bullets"][0], size=24, hex_color="94a3b8", align="center",
        )
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_objectives(deck, spec: dict, t: dict) -> None:
    """Learning objectives: numbered cards on light background."""
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_white"])
    _add_rect(slide, 0, 0, SLIDE_W, 1.2, t["primary"])
    _add_text(
        slide, 0.5, 0.25, SLIDE_W - 1, 0.8,
        spec.get("title", "Learning Objectives"),
        size=32, bold=True, hex_color=t["light_text"],
    )
    bullets = spec.get("bullets", [])
    y = 1.6
    for i, b in enumerate(bullets[:6]):
        _add_rounded(slide, 0.75, y, SLIDE_W - 1.5, 0.85, t["bg_light"])
        _add_text(
            slide, 1.0, y + 0.15, SLIDE_W - 2, 0.55,
            f"{i+1}.  {b}", size=20, hex_color=t["dark_text"],
        )
        y += 0.95
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_exit_ticket(deck, spec: dict, t: dict) -> None:
    """Exit ticket: single reflective prompt on accent-tinted background."""
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_summary"])
    _add_rect(slide, 0, 0, SLIDE_W, 0.12, t["primary"])
    _add_rect(slide, 0, SLIDE_H - 0.12, SLIDE_W, 0.12, t["primary"])
    _add_text(
        slide, 1, 1.5, SLIDE_W - 2, 1.0,
        spec.get("title", "Exit Ticket"),
        size=36, bold=True, hex_color=t["dark_text"], align="center",
    )
    if spec.get("bullets"):
        _add_text(
            slide, 1.5, 3.0, SLIDE_W - 3, 2.5,
            spec["bullets"][0], size=24, hex_color=t["dark_text"], align="center",
        )
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_content(deck, spec: dict, t: dict) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_white"])
    _add_rect(slide, 0, 0, SLIDE_W, 1.2, t["primary"])
    _add_text(
        slide, 0.5, 0.25, SLIDE_W - 1, 0.8,
        spec.get("title", ""),
        size=32, bold=True, hex_color=t["light_text"],
    )
    _add_rect(slide, 0, 1.2, 0.12, SLIDE_H - 1.2, t["accent"])
    _add_bullets(slide, 0.75, 1.6, SLIDE_W - 1.5, 5.0, spec.get("bullets", []))
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_two_column(deck, spec: dict, t: dict) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_white"])
    _add_rect(slide, 0, 0, SLIDE_W, 1.2, t["secondary"])
    _add_text(
        slide, 0.5, 0.25, SLIDE_W - 1, 0.8,
        spec.get("title", ""),
        size=32, bold=True, hex_color=t["light_text"],
    )
    bullets = spec.get("bullets", [])
    half = (len(bullets) + 1) // 2
    left_col = bullets[:half]
    right_col = bullets[half:]
    _add_rounded(slide, 0.5, 1.6, SLIDE_W / 2 - 0.75, 5.4, t["bg_light"])
    _add_rounded(slide, SLIDE_W / 2 + 0.25, 1.6, SLIDE_W / 2 - 0.75, 5.4, t["bg_light"])
    _add_bullets(slide, 1.0, 1.9, SLIDE_W / 2 - 1.5, 4.8, left_col)
    _add_bullets(slide, SLIDE_W / 2 + 0.75, 1.9, SLIDE_W / 2 - 1.5, 4.8, right_col)
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_image(deck, spec: dict, t: dict, image_path: Path | None) -> None:
    from pptx.util import Inches
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_white"])
    _add_rect(slide, 0, 0, SLIDE_W, 1.2, t["primary"])
    _add_text(
        slide, 0.5, 0.25, SLIDE_W - 1, 0.8,
        spec.get("title", ""),
        size=32, bold=True, hex_color=t["light_text"],
    )
    if image_path and image_path.exists():
        try:
            slide.shapes.add_picture(
                str(image_path),
                Inches(3.0), Inches(1.6),
                width=Inches(7.3), height=Inches(5.4),
            )
        except Exception as exc:
            logger.warning("Could not add picture for slide %s: %s", spec.get("slide_number"), exc)
    else:
        _add_text(
            slide, 3.0, 3.5, 7.3, 0.8,
            "(image unavailable)",
            size=16, hex_color="94a3b8", align="center",
        )
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_quote(deck, spec: dict, t: dict) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["dark_bg"])
    _add_rect(slide, 0.5, 3.0, 0.15, 1.5, t["accent"])
    _add_text(
        slide, 1.0, 2.6, SLIDE_W - 2, 2.4,
        spec.get("quote_text") or (spec.get("bullets", [""])[0] if spec.get("bullets") else ""),
        size=28, bold=False, hex_color=t["light_text"],
    )
    author = spec.get("quote_author", "")
    if author:
        _add_text(
            slide, 1.0, 5.4, SLIDE_W - 2, 0.6,
            f"— {author}", size=18, hex_color="94a3b8",
        )
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_quiz(deck, spec: dict, t: dict) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_quiz"])
    _add_rect(slide, 0, 0, SLIDE_W, 1.2, t["accent"])
    _add_text(
        slide, 0.5, 0.25, SLIDE_W - 1, 0.8,
        f"Q: {spec.get('title', 'Question')}",
        size=30, bold=True, hex_color=t["dark_text"],
    )
    _add_bullets(slide, 0.75, 1.6, SLIDE_W - 1.5, 5.0, spec.get("bullets", []), hex_color=t["dark_text"])
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_summary(deck, spec: dict, t: dict) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_summary"])
    _add_rect(slide, 0, 0, SLIDE_W, 1.2, t["primary"])
    _add_text(
        slide, 0.5, 0.25, SLIDE_W - 1, 0.8,
        spec.get("title", "Summary"),
        size=32, bold=True, hex_color=t["light_text"],
    )
    _add_bullets(slide, 0.75, 1.6, SLIDE_W - 1.5, 5.0, spec.get("bullets", []))
    _add_notes(slide, spec.get("speaker_notes", ""))


def _render_activity(deck, spec: dict, t: dict) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    _set_bg(slide, t["bg_light"])
    _add_rect(slide, 0, 0, SLIDE_W, 1.2, t["secondary"])
    _add_text(
        slide, 0.5, 0.25, SLIDE_W - 1, 0.8,
        f"Activity: {spec.get('title', '')}",
        size=30, bold=True, hex_color=t["light_text"],
    )
    _add_bullets(slide, 0.75, 1.6, SLIDE_W - 1.5, 5.0, spec.get("bullets", []))
    _add_notes(slide, spec.get("speaker_notes", ""))


_LAYOUTS = {
    "title":       _render_title,
    "hook":        _render_hook,
    "objectives":  _render_objectives,
    "exit_ticket": _render_exit_ticket,
    "content":     _render_content,
    "two_column":  _render_two_column,
    "image":       _render_image,
    "quote":       _render_quote,
    "quiz":        _render_quiz,
    "summary":     _render_summary,
    "activity":    _render_activity,
}


def _build_deck(plan: list[dict], image_paths: dict[int, Path], output_path: Path, theme_name: str = "emerald") -> None:
    """Deterministic build. Same plan + same images => same deck, every time."""
    from pptx import Presentation
    from pptx.util import Inches

    t = THEMES.get(theme_name, PALETTE)

    deck = Presentation()
    deck.slide_width = Inches(SLIDE_W)
    deck.slide_height = Inches(SLIDE_H)

    for i, spec in enumerate(plan):
        layout = str(spec.get("layout", "content")).lower()
        try:
            if layout == "image":
                _render_image(deck, spec, t, image_paths.get(spec.get("slide_number", i + 1)))
            else:
                renderer = _LAYOUTS.get(layout, _render_content)
                renderer(deck, spec, t)
        except Exception as exc:
            logger.error("Layout '%s' failed for slide %d: %s", layout, i + 1, exc)
            # Fall back to a content-style slide so the deck still ships.
            _render_content(deck, spec, t)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(output_path))


async def build_presentation(
    plan: list[dict],
    image_paths: dict[int, Path],
    run_id: str,
    theme: str = "emerald",
) -> Path:
    """Build the .pptx file. Public entry-point kept identical to the old API."""
    work_dir = PPTX_DIR / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / "presentation.pptx"

    # python-pptx is synchronous; hand it to a worker thread so we do not
    # block the event loop while the file is written.
    await asyncio.to_thread(_build_deck, plan, image_paths, output_path, theme)
    await _generate_thumbnail(output_path)
    return output_path


# ---------------------------------------------------------------------------
# Thumbnail -- unchanged behaviour, kept public for the Files page
# ---------------------------------------------------------------------------

async def _generate_thumbnail(pptx_path: Path) -> Path | None:
    """First-slide thumbnail via python-pptx + Pillow. Best-effort."""
    thumb_path = pptx_path.parent / "thumbnail.png"

    def _work() -> Path | None:
        from PIL import Image, ImageDraw, ImageFont
        from pptx import Presentation

        prs = Presentation(str(pptx_path))
        if not prs.slides:
            return None
        slide = prs.slides[0]

        bg_color = (240, 240, 240)
        try:
            fill = slide.background.fill
            if fill.type is not None:
                try:
                    rgb = fill.fore_color.rgb
                    bg_color = (rgb[0], rgb[1], rgb[2])
                except Exception:
                    pass
        except Exception:
            pass

        title = "Presentation"
        try:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    title = shape.text.strip()[:40]
                    break
        except Exception:
            pass

        img = Image.new("RGB", (800, 450), color=bg_color)
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40,
            )
        except Exception:
            pass

        luminance = 0.299 * bg_color[0] + 0.587 * bg_color[1] + 0.114 * bg_color[2]
        text_color = (0, 0, 0) if luminance > 128 else (255, 255, 255)

        bbox = draw.textbbox((0, 0), title, font=font)
        x = (800 - (bbox[2] - bbox[0])) // 2
        y = (450 - (bbox[3] - bbox[1])) // 2
        draw.text((x, y), title, fill=text_color, font=font)

        img.save(str(thumb_path), "PNG")
        return thumb_path

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.error("Thumbnail generation failed: %s", exc)
        return None
