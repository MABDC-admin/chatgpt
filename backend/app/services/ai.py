"""Thin wrapper over the OpenAI client plus the cost-optimisation router."""

import re
from dataclasses import dataclass
from pathlib import Path

from openai import AsyncOpenAI

from app.config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)

# Cheap model is enough for short, factual, low-stakes turns. Anything that
# smells like lesson design, analysis or long-form writing goes to the full
# model -- a wrong answer there costs a teacher more than the token saving.
_HEAVY_HINTS = re.compile(
    r"\b(lesson plan|unit plan|assessment|rubric|curriculum|analy[sz]e|"
    r"evaluate|essay|report|worksheet|presentation|differentiat|scheme of work)\b",
    re.IGNORECASE,
)


_REASONING_EFFORTS = {"low", "medium", "high"}


@dataclass(frozen=True)
class TextRoute:
    model: str
    reasoning_effort: str
    label: str


def resolve_text_route(
    prompt: str,
    *,
    mode: str | None = None,
    reasoning_effort: str | None = None,
) -> TextRoute:
    """Resolve a user-selected route without exposing provider model IDs in the UI."""
    requested_mode = (mode or "auto").strip().lower()
    requested_effort = (reasoning_effort or "").strip().lower()
    effort = requested_effort if requested_effort in _REASONING_EFFORTS else None

    if requested_mode == "luna":
        return TextRoute(settings.cheap_text_model, effort or "medium", "Luna")
    if requested_mode == "terra":
        return TextRoute(settings.text_model, effort or "high", "Thinking")
    if requested_mode == "sol":
        return TextRoute(settings.pro_text_model, effort or "high", "Pro")

    if len(prompt) < 280 and not _HEAVY_HINTS.search(prompt):
        return TextRoute(settings.cheap_text_model, effort or "medium", "Auto")
    return TextRoute(settings.text_model, effort or "medium", "Auto")


def route_text_model(prompt: str, requested: str | None = None) -> str:
    """Pick a text model. An explicit, known request always wins."""
    if requested in (settings.text_model, settings.cheap_text_model, settings.pro_text_model):
        return requested
    return resolve_text_route(prompt).model


_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.txt"

_FALLBACK_PROMPT = (
    "You are the AI assistant for a school, used by teachers, administrators and "
    "students. Be accurate and concise, and say when you are unsure rather than guessing."
)


def _load_system_prompt() -> str:
    """Read the operating prompt from disk.

    Kept as a text file rather than a Python string so it can be reviewed and
    edited without touching code. A missing file must not take the chat down,
    so it degrades to a minimal prompt instead of raising at import time.
    """
    try:
        text = _PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_PROMPT
    return text or _FALLBACK_PROMPT


_FORMATTING_APPENDIX = """

# FORMATTING

You render in the chat as GitHub-Flavoured Markdown. Use structure to help the reader, not to decorate. Rules:

- Open non-trivial answers with a short paragraph, then use `##` and `###` headings for anything longer than a screen.
- Use tables to compare things, list options, or map A to B. Prefer a table over a bulleted list of "X: Y" pairs.
- Use bullet lists for parallel items; use numbered lists only for ordered steps.
- Use fenced code blocks with a language tag for anything the user might copy.
- Use `> quotes` to highlight a key definition or takeaway.
- Use `**bold**` on the phrase a reader would scan for, sparingly.
- Two-column layout for compare-and-contrast. Write the block directly in the message -- NOT inside a fenced code block, or it will render as literal text and defeat the purpose. Use exactly this shape, with `~~~` on its own line as the divider:

::: columns
## Pros
- point
- point
~~~
## Cons
- point
- point
:::

- Inline illustration icons for lightweight visual anchors. The shortcode is literally `:icon:NAME:` -- the word `icon` is required, not decorative. `:icon:bulb:` renders a lightbulb; `:bulb:` alone renders nothing and the raw text will show. Available NAMEs: sun, leaf, water, cloud, book, bulb, check, cross, info, warn, target, spark, atom, globe, ruler, flask. One or two per answer at most; never inside a heading.

Keep answers direct and short by default. Only reach for a heavy structure when the content earns it -- a two-line answer stays two lines."""


SYSTEM_PROMPT = _load_system_prompt() + _FORMATTING_APPENDIX



def _supports_legacy_params(model: str) -> bool:
    """GPT-5-era models reject `max_tokens` and any temperature but the default.

    Verified against the live API: gpt-5.6-* returns 400 for `temperature=0`
    ("Only the default (1) value is supported") and for `max_tokens`
    ("Use 'max_completion_tokens' instead"). Older gpt-4* models accept both.
    """
    return model.startswith(("gpt-4", "gpt-3"))


def completion_kwargs(
    model: str,
    *,
    max_output: int | None = None,
    deterministic: bool = False,
    reasoning_effort: str | None = None,
) -> dict:
    """Build per-model kwargs so one call site works across model generations.

    Note on `max_output` for GPT-5-era models: `max_completion_tokens` caps
    reasoning tokens *and* visible output together. A budget that only covers
    the expected answer gets spent entirely on reasoning and the call returns
    empty content with finish_reason="length" -- a silent failure, since no
    exception is raised. Measured reasoning overhead on gpt-5.6-luna for short
    instruction-following prompts is ~30 tokens, so callers must leave room for
    that on top of the answer they actually want.
    """
    kwargs: dict = {}
    if max_output is not None:
        key = "max_tokens" if _supports_legacy_params(model) else "max_completion_tokens"
        kwargs[key] = max_output
    if deterministic and _supports_legacy_params(model):
        kwargs["temperature"] = 0
    if reasoning_effort in _REASONING_EFFORTS and model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = reasoning_effort
    return kwargs


_TITLE_PROMPT = (
    "Write a 3-6 word title for a conversation that opens with the message below. "
    "Describe the subject, not the request. For example if the message is about "
    "helping write a story, title it 'Creative Writing Help', not 'User asks for help'. "
    "If the message is a greeting or too short to determine a subject, use 'New Conversation'. "
    "No quotes, no trailing full stop, Title Case."
)


async def generate_title(first_message: str) -> str:
    """Name a conversation from its opening message.

    Falls back to a truncation of the message itself, which is what the user
    would otherwise have seen, so a provider hiccup costs nothing.
    """
    fallback = first_message.strip()[:60] or "New chat"
    try:
        completion = await client.chat.completions.create(
            model=settings.cheap_text_model,
            messages=[
                {"role": "system", "content": _TITLE_PROMPT},
                {"role": "user", "content": first_message[:1000]},
            ],
            # A title is ~10 tokens, but reasoning takes ~30 before any of it
            # is written. Billing is on tokens used, not on the cap, so the
            # headroom is free.
            **completion_kwargs(settings.cheap_text_model, max_output=200),
        )
        title = (completion.choices[0].message.content or "").strip().strip('"')
        return title[:60] or fallback
    except Exception:
        return fallback


INTENT_TEXT = "TEXT"
INTENT_IMAGE_NEW = "IMAGE_NEW"
INTENT_IMAGE_EDIT = "IMAGE_EDIT"
INTENT_PPT = "PPT"

_PPT_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "ppt_master_prompt.txt"


def _load_ppt_prompt() -> str:
    try:
        return _PPT_PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


PPT_MASTER_PROMPT = _load_ppt_prompt()

_INTENT_PROMPT = """You route messages in a school assistant that can chat, create images, and generate PowerPoint presentations. Read the user's latest message and answer with exactly one word.

IMAGE_NEW - the user is ASKING YOU TO MAKE a new picture, poster, diagram, illustration, mockup or UI design right now.
IMAGE_EDIT - the user wants the PREVIOUS image in this conversation changed (colour, text, layout, background, style, adding or removing elements, quality, repositioning).
PPT - the user wants a PowerPoint presentation, .pptx file, or slide deck created.
TEXT - anything else, including every question ABOUT images, presentations, or about you.

Critical distinction. A question about your abilities is TEXT, not a request to create. The user wants an answer, not a file. These are all TEXT:
  "can you generate images?"           -> TEXT
  "are you able to edit photos?"       -> TEXT
  "do you support image generation?"   -> TEXT
  "what image models can you use?"     -> TEXT
  "how do I make a poster here?"       -> TEXT
  "what size images can you make?"     -> TEXT
  "can you make powerpoints?"          -> TEXT
  "do you support ppt generation?"     -> TEXT
  "what is a ppt?"                     -> TEXT

These are IMAGE_NEW, because they are instructions to produce something:
  "create a Sportsfest registration UI"   -> IMAGE_NEW
  "make a poster about recycling"         -> IMAGE_NEW
  "draw the water cycle for Year 5"       -> IMAGE_NEW
  "I need a diagram of a plant cell"      -> IMAGE_NEW

These are PPT, because they ask for a presentation or slide deck:
  "make me a ppt about photosynthesis"       -> PPT
  "create a 10-slide presentation on WW2"    -> PPT
  "generate a powerpoint for my lesson"      -> PPT
  "build a slide deck about climate change"  -> PPT
  "I need a pptx on the solar system"        -> PPT
  "make a presentation about animals"        -> PPT

These are IMAGE_EDIT when a previous image exists:
  "make it blue"                  -> IMAGE_EDIT
  "remove the sidebar"            -> IMAGE_EDIT
  "add the MABDC logo top left"   -> IMAGE_EDIT

Rules:
- If the message is phrased as a question and does not give a subject to create, choose TEXT.
- Choose IMAGE_EDIT only when a previous image exists in this conversation.
- PPT takes priority over IMAGE_NEW when the user mentions presentations, slides, ppt, pptx, or powerpoint.
- When genuinely unsure, choose TEXT.

Answer with the single word and nothing else."""


async def classify_intent(message: str, *, has_previous_image: bool) -> str:
    """Decide whether a turn is chat, a new image, or an edit of the last one.

    A cheap model call rather than keyword matching: "make it blue" and "add the
    logo" carry no image vocabulary at all, and regexes misroute them.
    Any failure falls back to TEXT, which is the harmless direction to be wrong in.
    """
    context = (
        "A previously generated image exists in this conversation."
        if has_previous_image
        else "There is no previous image in this conversation, so IMAGE_EDIT is not valid."
    )
    try:
        completion = await client.chat.completions.create(
            model=settings.cheap_text_model,
            messages=[
                {"role": "system", "content": _INTENT_PROMPT},
                {"role": "user", "content": f"{context}\n\nLatest message: {message}"},
            ],
            # The answer is one word, but the cap also covers reasoning tokens.
            # Too tight a budget returns empty content instead of raising, and
            # this function's fallback is TEXT -- which would silently disable
            # image generation entirely. Headroom is billed only if used.
            **completion_kwargs(
                settings.cheap_text_model, max_output=200, deterministic=True
            ),
        )
        answer = (completion.choices[0].message.content or "").strip().upper()
    except Exception:
        return INTENT_TEXT

    if INTENT_PPT in answer:
        return INTENT_PPT
    if INTENT_IMAGE_EDIT in answer and has_previous_image:
        return INTENT_IMAGE_EDIT
    if INTENT_IMAGE_NEW in answer:
        return INTENT_IMAGE_NEW
    if INTENT_IMAGE_EDIT in answer:
        # Model wanted an edit but nothing exists to edit yet.
        return INTENT_IMAGE_NEW
    return INTENT_TEXT
