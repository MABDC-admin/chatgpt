"""Expand terse image prompts into detailed visual descriptions.

ChatGPT internally rewrites user prompts before sending them to the image
model: "poster about recycling" becomes a paragraph describing layout,
colours, typography and style. This gives the model enough detail to produce
a professional result on the first try rather than requiring two or three
revision rounds.

Cost: one cheap-model call (~0.05¢) per image generation.
"""

import json

from app.config import settings
from app.services.ai import client, completion_kwargs

_EXPAND_PROMPT = """You are an expert visual designer writing an image-generation brief.

The user gave a short request. Rewrite it into a detailed visual prompt of 60–120 words that a text-to-image model can follow. Include:
- Subject and composition (what appears where)
- Art style or medium (flat illustration, photorealistic, watercolour, infographic, UI mockup…)
- Colour palette or mood
- Typography direction if text appears in the image
- Background treatment

Keep the user's original intent. Do not add elements the user didn't ask for.
If the user's request is ALREADY detailed (over 80 words), return it unchanged.
Output ONLY the expanded prompt — no explanation, no preamble."""

# When the user asks for an image mid-conversation (e.g. "turn that into an
# infographic"), the cheap model needs to see what "that" refers to.  This
# variant includes recent conversation history as context.
_EXPAND_WITH_CONTEXT_PROMPT = """You are an expert visual designer writing an image-generation brief.

Below is the recent conversation between the user and an AI assistant. The user's LATEST message asks for an image to be generated, but it may refer to content discussed earlier in the conversation (e.g. "turn that into an infographic", "make a poster about what we just discussed").

Your job: Write a detailed visual prompt of 60–120 words that a text-to-image model can follow. You MUST incorporate the specific subject matter from the conversation — not just the user's latest words in isolation.

Include:
- Subject and composition drawn from the CONVERSATION CONTEXT (what actually appeared where)
- Art style or medium the user requested (infographic, handwritten, poster, etc.)
- Colour palette or mood
- Typography direction if text appears in the image
- Background treatment

If the user's latest message is ALREADY detailed and self-contained (over 80 words), return it unchanged.
Output ONLY the expanded prompt — no explanation, no preamble."""


async def expand_image_prompt(raw: str, *, context_messages: list[dict] | None = None) -> str:
    """Return an enriched prompt. Falls back to the original on any error.

    When *context_messages* is provided (recent conversation history), the
    cheap model can resolve references like "turn that into an infographic"
    by seeing what "that" actually is.
    """
    if len(raw.split()) > 80:
        return raw
    try:
        if context_messages:
            # Build messages: system prompt + conversation history + final user request
            messages = [
                {"role": "system", "content": _EXPAND_WITH_CONTEXT_PROMPT},
                *context_messages[-6:],  # last 3 turns (6 messages) for context
                {"role": "user", "content": raw},
            ]
        else:
            messages = [
                {"role": "system", "content": _EXPAND_PROMPT},
                {"role": "user", "content": raw},
            ]
        result = await client.chat.completions.create(
            model=settings.cheap_text_model,
            messages=messages,
            # 60-120 words of output is ~160 tokens, and reasoning consumes a
            # further ~30 before any of it is emitted. Too tight a cap returns
            # empty content rather than raising, so leave generous headroom.
            **completion_kwargs(settings.cheap_text_model, max_output=600),
        )
        expanded = (result.choices[0].message.content or "").strip()
        return expanded if expanded else raw
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# Bulk slide generation: parse an outline into individual image prompts
# ---------------------------------------------------------------------------

_SLIDE_PARSE_PROMPT = """You are an expert presentation designer and visual prompt engineer.

The user previously generated a slide outline for a presentation. Now they want each slide as a separate image.

Your job: Read the outline and produce a JSON array where each element is a detailed visual prompt (60-100 words) for ONE slide. Each prompt must describe exactly what should appear in that slide's image.

CRITICAL STYLE REQUIREMENTS — every prompt MUST include these style keywords:
"cartoon style, playful, colorful, flat illustration, cute characters, educational infographic layout, suitable for teacher presentation, bright colors, clear visuals"

Rules:
- One array element per slide from the outline
- Each prompt should describe the VISUAL content of that slide (characters, icons, layout, colors, text labels)
- Include any specific topic details from the outline (e.g., if slide 3 is about Air Pollution, mention factories, cars, smog)
- Do NOT include markdown formatting, code fences, or explanations
- Output ONLY a valid JSON array of strings

Example output:
["Slide 1 title card with the word 'Pollution' in big playful cartoon letters, a smiling Earth character wearing sunglasses, bright blue sky background with fluffy white clouds, colorful flowers at the bottom, cartoon style, playful, flat illustration, cute characters, educational infographic layout, suitable for teacher presentation", "Slide 2 showing types of pollution with four cartoon icons arranged in a grid: a factory puffing gray smoke for air, a pipe dripping green goo into a river for water, a trash pile for soil, and a speaker blasting sound waves for noise, bright yellow background, cartoon style, playful, colorful, flat illustration, educational infographic layout"]
"""


async def parse_slides_outline(outline_text: str) -> list[str]:
    """Parse a presentation outline into individual slide image prompts.

    Returns a list of visual prompts, one per slide, with cartoon/presentation
    style keywords baked in. Falls back to an empty list on any error.
    """
    try:
        result = await client.chat.completions.create(
            model=settings.cheap_text_model,
            messages=[
                {"role": "system", "content": _SLIDE_PARSE_PROMPT},
                {"role": "user", "content": outline_text},
            ],
            **completion_kwargs(settings.cheap_text_model, max_output=4000),
        )
        content = (result.choices[0].message.content or "").strip()
        # Extract JSON array from response (handle possible markdown fences)
        start = content.find("[")
        end = content.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        slides = json.loads(content[start : end + 1])
        if isinstance(slides, list) and all(isinstance(s, str) for s in slides):
            return slides
        return []
    except Exception:
        return []
