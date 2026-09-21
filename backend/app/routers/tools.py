"""Teacher AI Tools — structured educational content generation.

Each tool takes a specification (topic, grade, format options) and streams
a structured response. They reuse the same SSE pattern as the chat endpoint
so the frontend can progressively render.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_db
from app.deps import require
from app.models import User
from app.rbac import Permission
from app.services import ai, export
from app.services.credits import assert_has_balance, record_usage
from app.services.errors import safe_message
from app.services.pricing import text_cost_cents
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class LessonPlanRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    grade_level: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(ge=5, le=480, default=60)
    standards: str = ""
    additional_notes: str = ""


class AssessmentRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    grade_level: str = Field(min_length=1, max_length=100)
    assessment_type: str = Field(default="quiz")  # quiz, test, rubric, exit-ticket
    question_count: int = Field(ge=1, le=100, default=10)
    additional_notes: str = ""


class WorksheetRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    grade_level: str = Field(min_length=1, max_length=100)
    worksheet_type: str = Field(default="practice")  # practice, review, homework, activity
    additional_notes: str = ""


_LESSON_PLAN_PROMPT = """You are an expert curriculum designer. Create a detailed, ready-to-use lesson plan.

Format using clear Markdown with these sections:
## Lesson Plan: {topic}

### Overview
- **Grade Level:** ...
- **Duration:** ... minutes
- **Standards Alignment:** ...
- **Learning Objectives:** (2-4 measurable objectives using Bloom's taxonomy verbs)

### Materials Needed
- Bulleted list

### Warm-Up / Opener (X minutes)
Step-by-step with timing

### Direct Instruction (X minutes)
Key concepts with suggested explanations

### Guided Practice (X minutes)
Activities with clear instructions

### Independent Practice (X minutes)
Student work with success criteria

### Closure / Assessment (X minutes)
How to check for understanding

### Differentiation
- **For struggling learners:** ...
- **For advanced learners:** ...
- **ELL supports:** ...

### Homework / Extension
Optional follow-up

Be specific, practical and immediately usable. Include actual example questions, discussion prompts and activity descriptions — not placeholders."""

_ASSESSMENT_PROMPT = """You are an expert in educational assessment design. Create a ready-to-use assessment.

Format using clear Markdown:

## {assessment_type}: {topic}

### Instructions
Clear student-facing directions.

### Questions
Number each question. Include:
- A mix of question types appropriate to the assessment type
- For multiple choice: 4 options (A-D) with the correct answer marked
- For short answer/essay: include point values and what a strong answer includes
- Bloom's taxonomy level for each question (in parentheses)

### Answer Key
All correct answers with brief explanations.

### Rubric (if applicable)
Scoring criteria with point breakdowns.

Be grade-appropriate. Questions should progress from recall to higher-order thinking."""

_WORKSHEET_PROMPT = """You are an expert teacher creating an engaging, print-ready worksheet.

Format using clear Markdown:

## {worksheet_type} Worksheet: {topic}

**Name:** _________________ **Date:** _________________ **Grade:** {grade_level}

### Instructions
Clear student-facing directions.

### Part 1: [Section Name]
Numbered items appropriate to the worksheet type.

### Part 2: [Section Name]
More items, increasing in complexity.

### Challenge / Bonus
One or two extension items for early finishers.

---
### Answer Key (Teacher Copy)
All answers.

Make it engaging and age-appropriate. Include enough items for a full class period. Use clear formatting that works when printed on paper."""


async def _stream_tool(
    system_prompt: str,
    user_message: str,
    user_id,
) -> AsyncIterator[str]:
    model = settings.text_model
    yield _sse("start", {"model": model, "kind": "tool"})

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    chunks: list[str] = []
    prompt_tokens = completion_tokens = 0

    try:
        stream = await ai.client.chat.completions.create(
            model=model, messages=messages, stream=True,
            stream_options={"include_usage": True},
        )
        async for event in stream:
            if event.usage:
                prompt_tokens = event.usage.prompt_tokens
                completion_tokens = event.usage.completion_tokens
            if event.choices and event.choices[0].delta.content:
                piece = event.choices[0].delta.content
                chunks.append(piece)
                yield _sse("delta", {"content": piece})
    except Exception as exc:
        yield _sse("error", {"message": safe_message(exc, action="The generation")})

    cost = text_cost_cents(model, prompt_tokens, completion_tokens)
    yield _sse("done", {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_cents": cost,
    })

    if chunks:
        async with SessionLocal() as session:
            await record_usage(
                session,
                user_id=user_id,
                kind="text",
                model=model,
                cost_cents=cost,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )


@router.post("/lesson-plan")
async def generate_lesson_plan(
    payload: LessonPlanRequest,
    user: User = Depends(require(Permission.AI_CHAT)),
    db: AsyncSession = Depends(get_db),
):
    await assert_has_balance(db, user.id)
    allowed, _ = await check_rate_limit(str(user.id), "tools")
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests -- slow down a moment"
        )

    user_msg = f"""Create a lesson plan with these specifications:
- Topic: {payload.topic}
- Grade Level: {payload.grade_level}
- Duration: {payload.duration_minutes} minutes
- Standards: {payload.standards or 'Not specified — use general best practices'}
- Additional notes: {payload.additional_notes or 'None'}"""

    return StreamingResponse(
        _stream_tool(_LESSON_PLAN_PROMPT, user_msg, user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/assessment")
async def generate_assessment(
    payload: AssessmentRequest,
    user: User = Depends(require(Permission.AI_CHAT)),
    db: AsyncSession = Depends(get_db),
):
    await assert_has_balance(db, user.id)
    allowed, _ = await check_rate_limit(str(user.id), "tools")
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests -- slow down a moment"
        )

    user_msg = f"""Create a {payload.assessment_type} with these specifications:
- Topic: {payload.topic}
- Grade Level: {payload.grade_level}
- Number of questions: {payload.question_count}
- Assessment type: {payload.assessment_type}
- Additional notes: {payload.additional_notes or 'None'}"""

    return StreamingResponse(
        _stream_tool(
            _ASSESSMENT_PROMPT.replace("{assessment_type}", payload.assessment_type.title()),
            user_msg,
            user.id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/worksheet")
async def generate_worksheet(
    payload: WorksheetRequest,
    user: User = Depends(require(Permission.AI_CHAT)),
    db: AsyncSession = Depends(get_db),
):
    await assert_has_balance(db, user.id)
    allowed, _ = await check_rate_limit(str(user.id), "tools")
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests -- slow down a moment"
        )

    user_msg = f"""Create a {payload.worksheet_type} worksheet with these specifications:
- Topic: {payload.topic}
- Grade Level: {payload.grade_level}
- Worksheet type: {payload.worksheet_type}
- Additional notes: {payload.additional_notes or 'None'}"""

    return StreamingResponse(
        _stream_tool(
            _WORKSHEET_PROMPT.replace("{worksheet_type}", payload.worksheet_type.title())
                             .replace("{grade_level}", payload.grade_level),
            user_msg,
            user.id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ExportRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    title: str = Field(default="Document", max_length=200)
    format: str = Field(default="docx")  # docx | pdf


_EXPORT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


@router.post("/export")
async def export_document(
    payload: ExportRequest,
    user: User = Depends(require(Permission.AI_CHAT)),
):
    """Turn generated Markdown into a downloadable DOCX or PDF.

    Pure formatting -- no model call, so this is not billed.
    """
    if payload.format not in _EXPORT_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "format must be 'docx' or 'pdf'"
        )

    try:
        if payload.format == "docx":
            data = export.to_docx(payload.content, payload.title)
        else:
            data = export.to_pdf(payload.content, payload.title)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, safe_message(exc, action="The export")
        )

    filename = export.safe_filename(payload.title, payload.format)
    return Response(
        content=data,
        media_type=_EXPORT_TYPES[payload.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class SlidesRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    grade_level: str = Field(min_length=1, max_length=100)
    slide_count: int = Field(ge=3, le=30, default=10)
    additional_notes: str = ""


_SLIDES_PROMPT = """You design classroom presentations. Produce a slide deck as JSON only.

Return a JSON object of exactly this shape:
{"subtitle": "short deck subtitle", "slides": [{"title": "Slide title", "bullets": ["point", "point"], "notes": "what the teacher says on this slide"}]}

Rules:
- 3 to 5 bullets per slide, each under 15 words. Bullets are prompts to speak from, not paragraphs.
- Speaker notes are 2-3 sentences of what the teacher actually says.
- Open with a hook or objectives slide; close with a summary or check-for-understanding slide.
- Age-appropriate language for the stated grade level.
- Output raw JSON with no markdown fences and no commentary."""


@router.post("/slides")
async def generate_slides(
    payload: SlidesRequest,
    user: User = Depends(require(Permission.AI_CHAT)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a PowerPoint deck and return the .pptx file itself.

    Unlike the other tools this does not stream: a .pptx cannot be built until
    the whole outline exists, so the response is the finished binary.
    """
    await assert_has_balance(db, user.id)
    allowed, _ = await check_rate_limit(str(user.id), "tools")
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests -- slow down a moment"
        )

    model = settings.text_model
    user_msg = (
        f"Topic: {payload.topic}\n"
        f"Grade level: {payload.grade_level}\n"
        f"Number of content slides: {payload.slide_count}\n"
        f"Additional notes: {payload.additional_notes or 'None'}"
    )

    try:
        completion = await ai.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SLIDES_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            **ai.completion_kwargs(model, max_output=8000),
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, safe_message(exc, action="Slide generation")
        )

    usage = completion.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    cost = text_cost_cents(model, prompt_tokens, completion_tokens)

    # Bill before parsing: the tokens were spent whether or not the JSON is valid.
    async with SessionLocal() as session:
        await record_usage(
            session,
            user_id=user.id,
            kind="text",
            model=model,
            cost_cents=cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    try:
        outline = json.loads(completion.choices[0].message.content or "{}")
        slides = outline.get("slides") or []
        if not slides:
            raise ValueError("no slides returned")
        data = export.to_pptx(slides, payload.topic, outline.get("subtitle", payload.grade_level))
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, safe_message(exc, action="Building the deck")
        )

    filename = export.safe_filename(payload.topic, "pptx")
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Cost-Cents": str(cost),
            "X-Slide-Count": str(len(slides)),
        },
    )
