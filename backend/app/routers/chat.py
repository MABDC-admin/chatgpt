import base64
import json
import re
import uuid
from pathlib import Path
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal, get_db
from app.deps import current_user, require
from app.models import Attachment, Conversation, GeneratedImage, Message, User
from app.rbac import Permission, has_permission
from app.schemas import (
    ChatRequest,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
)
from app.services import ai, imaging, pptgen
from app.services.credits import assert_has_balance, record_usage
from app.services.errors import safe_message
from app.services.embeddings import search_similar
from app.services.pricing import image_cost_cents, image_cost_from_usage, text_cost_cents
from app.services.prompt_builder import expand_image_prompt, parse_slides_outline
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/api", tags=["chat"])

MAX_CONTEXT_MESSAGES = 20


async def _record_generated_attachment(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    file_path: Path,
    prompt: str,
) -> None:
    """Create an Attachment row for a generated image so it appears in /files."""
    size_bytes = file_path.stat().st_size if file_path.exists() else 0
    att = Attachment(
        user_id=user_id,
        filename=file_path.name,
        mime_type="image/png",
        kind="image",
        size_bytes=size_bytes,
        storage_path=str(file_path),
        source_type="generated",
        conversation_id=conversation_id,
        original_filename=f"Generated: {prompt[:80]}",
    )
    session.add(att)


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    q: str | None = None,
    archived: bool = False,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's conversations, newest first.

    `q` searches titles and message bodies, so a chat can be found by what was
    said in it and not only by the title it was given.
    """
    stmt = (
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            Conversation.archived.is_(archived),
        )
        .order_by(desc(Conversation.updated_at))
        .limit(100)
    )

    term = (q or "").strip()
    if term:
        pattern = f"%{term}%"
        matches_message = (
            select(Message.conversation_id)
            .where(
                Message.conversation_id == Conversation.id,
                Message.content.ilike(pattern),
            )
            .exists()
        )
        stmt = stmt.where(Conversation.title.ilike(pattern) | matches_message)

    rows = await db.scalars(stmt)
    return list(rows)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a conversation, or archive/restore it."""
    convo = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if convo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    if payload.title is not None:
        convo.title = payload.title.strip()[:255]
    if payload.archived is not None:
        convo.archived = payload.archived

    await db.commit()
    await db.refresh(convo)
    return convo


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    )
    if convo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return convo


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    await db.commit()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: User = Depends(require(Permission.AI_CHAT)),
    db: AsyncSession = Depends(get_db),
):
    """One conversational endpoint for text, new images and image revisions.

    Text and pictures share a thread: an image turn is stored as an assistant
    message carrying image_url, so the next turn can revise it without the user
    re-uploading or re-describing anything.
    """
    await assert_has_balance(db, user.id)

    allowed, _ = await check_rate_limit(str(user.id), "chat")
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests — slow down a moment")

    attachments = await _load_attachments(db, payload.attachment_ids, user.id)
    uploaded_images = [a for a in attachments if a.kind == "image"]

    history: list[dict] = []
    last_image_url: str | None = None
    is_new_conversation = False

    if payload.conversation_id:
        convo = await db.scalar(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == user.id,
            )
        )
        if convo is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        for message in convo.messages:
            if message.image_url:
                last_image_url = message.image_url
        history = await _build_history(db, convo.messages[-MAX_CONTEXT_MESSAGES:], user.id)
    else:
        convo = Conversation(
            user_id=user.id,
            model=ai.route_text_model(payload.message, payload.model),
            # Provisional: replaced with an AI-written title once the turn
            # finishes, so the sidebar has a label immediately either way.
            title=payload.message[:60].strip() or "New chat",
        )
        db.add(convo)
        await db.flush()
        is_new_conversation = True
        # Never touch convo.messages after a flush: the collection is unloaded
        # and would emit a lazy SELECT from sync context (MissingGreenlet).

    may_use_images = has_permission(user.role, Permission.AI_IMAGE)
    # Collect ALL uploaded images so multi-image prompts (e.g. "combine these
    # 3 pictures") actually reach the model.  Fall back to the last generated
    # image in the conversation when nothing was uploaded this turn.
    edit_sources: list[Path] = (
        [Path(img.storage_path) for img in uploaded_images]
        if uploaded_images
        else [imaging.path_from_url(last_image_url)]
        if last_image_url
        else []
    )

    # ------------------------------------------------------------------
    # Route order matters. Three overlapping paths compete for a single
    # message; measured misroutes forced this order and this tightening:
    #
    #   1. PPT keywords ("make me a PPT about volcanoes")
    #      -> build a deck via _ppt_turn
    #   2. BULK image intent ("each slide as an image", "one image per slide")
    #      -> generate N standalone images via _bulk_image_turn
    #   3. Fall through to the AI intent classifier for image vs text
    #
    # Bulk previously matched any digit + "slides"/"images", which routed
    # "there are 8 slides in the PISA framework, summarize them" to a bulk
    # image job. It now requires explicit image intent to fire.
    # ------------------------------------------------------------------
    import re
    _PPT_PATTERNS = [
        r"(?:make|create|generate|build)\s+(?:a\s+|me\s+a\s+)?(?:ppt|pptx|powerpoint|presentation|slide\s*deck)",
        r"(?:ppt|pptx|powerpoint|presentation|slide\s*deck)\s+(?:about|on|for|of)",
        r"(?:need|want)\s+(?:a\s+)?(?:ppt|pptx|powerpoint|presentation)",
        # "Create 5 slides about volcanoes" -- a deck request, not bulk images.
        r"(?:make|create|generate|build)\s+\d+\s+slides?\s+(?:about|on|for|of)",
    ]
    _BULK_PATTERNS = [
        r"all\s+slides\s+as\s+images",
        r"each\s+slide\s+as\s+(?:an?\s+)?image",
        r"one\s+image\s+per\s+slide",
        r"turn\s+each\s+slide.{0,40}\bimages?\b",
        r"slide\s+by\s+slide\s+as\s+images",
        r"bulk\s+image",
        r"(?:generate|create|make)\s+\d+\s+images?\b(?!\s+per\s+slide)",
    ]
    msg_lower = payload.message.lower()
    is_ppt_keyword = any(re.search(p, msg_lower) for p in _PPT_PATTERNS)
    is_bulk_request = (not is_ppt_keyword) and any(re.search(p, msg_lower) for p in _BULK_PATTERNS)

    if is_bulk_request and may_use_images and history:
        # Find the most recent assistant message containing a slide outline
        outline_text = ""
        for hmsg in reversed(history):
            if hmsg.get("role") == "assistant":
                c = hmsg.get("content", "")
                if isinstance(c, str) and ("Slide 1" in c or "slide 1" in c or "Title Slide" in c):
                    outline_text = c
                    break
        if outline_text:
            slide_prompts = await parse_slides_outline(outline_text)
            if slide_prompts:
                # Still persist the user message before streaming
                user_content = payload.message
                if attachments:
                    att_ids = [str(a.id) for a in attachments]
                    user_content = f"{payload.message}\n<!-- attachments: {json.dumps(att_ids)} -->"
                db.add(Message(conversation_id=convo.id, role="user", content=user_content))
                await db.commit()

                return StreamingResponse(
                    _bulk_image_turn(
                        slide_prompts=slide_prompts,
                        conversation_id=convo.id,
                        user_id=user.id,
                        size=payload.size,
                        quality=payload.quality,
                        model=payload.image_model,
                        title_from=payload.message if is_new_conversation else None,
                    ),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

    intent = await ai.classify_intent(
        payload.message, has_previous_image=bool(edit_sources) and may_use_images
    )
    if not may_use_images:
        intent = ai.INTENT_TEXT

    # Keyword match OR AI classifier says PPT → route to ppt_turn
    if is_ppt_keyword or intent == ai.INTENT_PPT:
        user_content = payload.message
        if attachments:
            att_ids = [str(a.id) for a in attachments]
            user_content = f"{payload.message}\n<!-- attachments: {json.dumps(att_ids)} -->"
        db.add(Message(conversation_id=convo.id, role="user", content=user_content))
        await db.commit()

        return StreamingResponse(
            _ppt_turn(
                user_message=payload.message,
                history=history,
                attachments=attachments,
                conversation_id=convo.id,
                user_id=user.id,
                image_model=payload.image_model,
                title_from=payload.message if is_new_conversation else None,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Persist attachment IDs alongside the user's text so subsequent turns can
    # reconstruct the full multimodal history.  Format is a JSON comment that
    # won't confuse the model if it leaks into context.
    user_content = payload.message
    if attachments:
        att_ids = [str(a.id) for a in attachments]
        user_content = f"{payload.message}\n<!-- attachments: {json.dumps(att_ids)} -->"

    db.add(Message(conversation_id=convo.id, role="user", content=user_content))
    await db.commit()

    conversation_id = convo.id
    user_id = user.id

    if intent in (ai.INTENT_IMAGE_NEW, ai.INTENT_IMAGE_EDIT):
        # When multiple images are uploaded, always route through edit() so all
        # references reach the model.  A single-image upload with no prior
        # generated image also goes through edit() when the user's prompt refers
        # to it; pure text-to-image stays on generate().
        if intent == ai.INTENT_IMAGE_NEW and not edit_sources:
            # Pass conversation history so the prompt builder can resolve
            # references like "turn each slide into an infographic" by seeing
            # what was actually discussed (e.g. a WWI worksheet).
            expanded = await expand_image_prompt(
                payload.message, context_messages=history if history else None
            )
        else:
            expanded = payload.message
            intent = ai.INTENT_IMAGE_EDIT if edit_sources else intent
        return StreamingResponse(
            _image_turn(
                intent=intent,
                instruction=expanded,
                source_paths=[str(p) for p in edit_sources] if edit_sources else [],
                conversation_id=conversation_id,
                user_id=user_id,
                size=payload.size,
                quality=payload.quality,
                model=payload.image_model,
                title_from=payload.message if is_new_conversation else None,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Attachments override length-based routing: a short question over a long
    # PDF or an image is a heavyweight task, however few words the user typed.
    if attachments and payload.model is None:
        text_model = settings.text_model
    else:
        text_model = ai.route_text_model(payload.message, payload.model)
    convo.model = text_model

    # RAG: pull relevant context from the school knowledge base.
    rag_context = ""
    if payload.use_knowledge_base:
        try:
            results = await search_similar(db, payload.message, limit=5)
            if results:
                snippets = "\n\n".join(
                    f"[From: {r['filename']} (relevance {r['score']})]\n{r['content']}"
                    for r in results
                )
                rag_context = (
                    "\n\nThe following excerpts from school documents may be relevant. "
                    "Use them to answer if applicable, and cite the source filename.\n\n"
                    + snippets
                )
        except Exception:
            pass  # RAG failure must not block chat

    await db.commit()

    system_content = ai.SYSTEM_PROMPT + rag_context
    user_content = _build_user_content(payload.message, attachments)
    messages = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": user_content},
    ]
    # Temporary debug: log message structure (not full base64) to verify
    # images are actually reaching the API call.
    import logging
    _dbg = logging.getLogger(__name__)
    for i, msg in enumerate(messages):
        c = msg["content"]
        if isinstance(c, list):
            types = [p.get("type") for p in c]
            _dbg.info("messages[%d] role=%s parts=%s", i, msg["role"], types)
        else:
            _dbg.info("messages[%d] role=%s text_len=%d", i, msg["role"], len(str(c)))
    return StreamingResponse(
        _text_turn(
            messages, text_model, conversation_id, user_id,
            title_from=payload.message if is_new_conversation else None,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _persist_and_bill(
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    content: str,
    image_url: str | None,
    kind: str,
    model: str,
    cost_cents: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    images: int = 0,
    title_from: str | None = None,
) -> None:
    """Store the assistant turn and charge for it.

    Runs on its own session: the request-scoped one is closing while the
    response streams.
    """
    async with SessionLocal() as session:
        session.add(
            Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                image_url=image_url,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )
        # Naming happens here rather than before the turn so the user waits on
        # the answer, not on the title.
        if title_from:
            convo = await session.get(Conversation, conversation_id)
            if convo is not None:
                convo.title = await ai.generate_title(title_from)
        await session.commit()
        await record_usage(
            session,
            user_id=user_id,
            kind=kind,
            model=model,
            cost_cents=cost_cents,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            images=images,
        )


async def _text_turn(
    messages: list[dict],
    model: str,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    title_from: str | None = None,
) -> AsyncIterator[str]:
    yield _sse("start", {"conversation_id": str(conversation_id), "model": model, "kind": "text"})

    chunks: list[str] = []
    prompt_tokens = completion_tokens = 0
    try:
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
            import logging, traceback
            logging.getLogger(__name__).error(
                "Text generation failed: %s\n%s", exc, traceback.format_exc()
            )
            yield _sse("error", {"message": safe_message(exc, action="The assistant reply")})

        cost = text_cost_cents(model, prompt_tokens, completion_tokens)
        yield _sse(
            "done",
            {
                "conversation_id": str(conversation_id),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_cents": cost,
            },
        )
    finally:
        # A closed tab cancels the generator mid-stream. Bill in `finally` so
        # tokens already spent with the provider are still recorded.
        answer = "".join(chunks)
        if answer:
            await _persist_and_bill(
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
                content=answer,
                image_url=None,
                kind="text",
                model=model,
                cost_cents=text_cost_cents(model, prompt_tokens, completion_tokens),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                title_from=title_from,
            )


async def _image_turn(
    *,
    intent: str,
    instruction: str,
    source_paths: list[str],
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    size: str | None,
    quality: str | None,
    model: str | None,
    title_from: str | None = None,
) -> AsyncIterator[str]:
    editing = intent == ai.INTENT_IMAGE_EDIT and bool(source_paths)
    img_size, img_quality = imaging.normalise(size, quality)
    img_model = imaging.default_model(model)

    yield _sse(
        "start",
        {
            "conversation_id": str(conversation_id),
            "model": img_model,
            "kind": "image_edit" if editing else "image",
        },
    )
    yield _sse(
        "status",
        {"message": "Revising the image…" if editing else "Creating the image…"},
    )

    cost = image_cost_cents(img_model, img_quality, img_size)

    try:
        async with SessionLocal() as session:
            account = await assert_has_balance(session, user_id)
            if account.monthly_allocation_cents and account.remaining_cents < cost:
                yield _sse(
                    "error",
                    {
                        "message": (
                            f"This image costs {cost}c but only "
                            f"{account.remaining_cents}c remain this month."
                        )
                    },
                )
                yield _sse("done", {"conversation_id": str(conversation_id), "cost_cents": 0})
                return

        if editing:
            paths = [Path(p) for p in source_paths]
            path, usage = await imaging.edit(
                paths,
                instruction,
                size=img_size,
                quality=img_quality,
                model=img_model,
            )
            caption = "Updated the image with your changes."
        else:
            path, usage = await imaging.generate(
                instruction, size=img_size, quality=img_quality, model=img_model
            )
            caption = "Here is the image."

        # Token-metered models return exact usage; flat-priced models do not,
        # and cost stays at the estimate computed above.
        actual = image_cost_from_usage(img_model, usage)
        if actual is not None:
            cost = actual
    except Exception as exc:
        import logging, traceback
        logging.getLogger(__name__).error(
            "Image generation failed: %s\n%s", exc, traceback.format_exc()
        )
        yield _sse("error", {"message": safe_message(exc, action="The image request")})
        yield _sse("done", {"conversation_id": str(conversation_id), "cost_cents": 0})
        return

    url = imaging.media_url(path)

    # Record in the gallery table and files system, so every picture --
    # however it was made -- shows up in one place.
    async with SessionLocal() as session:
        session.add(
            GeneratedImage(
                user_id=user_id,
                prompt=instruction,
                model=img_model,
                size=img_size,
                quality=img_quality,
                file_path=str(path),
                cost_cents=cost,
            )
        )
        await _record_generated_attachment(
            session, user_id, conversation_id, path, instruction
        )
        await session.commit()

    await _persist_and_bill(
        conversation_id=conversation_id,
        user_id=user_id,
        role="assistant",
        content=caption,
        image_url=url,
        kind="image",
        model=img_model,
        cost_cents=cost,
        images=1,
        title_from=title_from,
    )

    yield _sse("image", {"url": url, "caption": caption, "edited": editing})
    yield _sse(
        "done",
        {"conversation_id": str(conversation_id), "cost_cents": cost, "image_url": url},
    )


async def _bulk_image_turn(
    *,
    slide_prompts: list[str],
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    size: str | None,
    quality: str | None,
    model: str | None,
    title_from: str | None = None,
) -> AsyncIterator[str]:
    """Generate one image per slide prompt, streaming progress as we go."""
    img_size, img_quality = imaging.normalise(size, quality)
    img_model = imaging.default_model(model)
    total = len(slide_prompts)

    yield _sse(
        "start",
        {
            "conversation_id": str(conversation_id),
            "model": img_model,
            "kind": "bulk_image",
            "total": total,
        },
    )

    total_cost = 0
    generated_urls: list[str] = []

    for i, prompt in enumerate(slide_prompts):
        yield _sse(
            "status",
            {"message": f"Generating slide {i + 1} of {total}\u2026"},
        )

        cost = image_cost_cents(img_model, img_quality, img_size)

        try:
            async with SessionLocal() as session:
                account = await assert_has_balance(session, user_id)
                if account.monthly_allocation_cents and account.remaining_cents < cost:
                    yield _sse(
                        "error",
                        {
                            "message": (
                                f"Not enough credits for slide {i + 1}. "
                                f"This image costs {cost}c but only "
                                f"{account.remaining_cents}c remain this month."
                            )
                        },
                    )
                    break

            path, usage = await imaging.generate(
                prompt, size=img_size, quality=img_quality, model=img_model
            )

            actual = image_cost_from_usage(img_model, usage)
            if actual is not None:
                cost = actual

            url = imaging.media_url(path)
            generated_urls.append(url)

            # Record in gallery and files system
            async with SessionLocal() as session:
                session.add(
                    GeneratedImage(
                        user_id=user_id,
                        prompt=prompt,
                        model=img_model,
                        size=img_size,
                        quality=img_quality,
                        file_path=str(path),
                        cost_cents=cost,
                    )
                )
                await _record_generated_attachment(
                    session, user_id, conversation_id, path, prompt
                )
                await session.commit()

            # Persist each slide as its own assistant message so they appear
            # in the conversation history individually.
            caption = f"Slide {i + 1} of {total}"
            await _persist_and_bill(
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
                content=caption,
                image_url=url,
                kind="image",
                model=img_model,
                cost_cents=cost,
                images=1,
                title_from=title_from if i == 0 else None,
            )

            total_cost += cost

            # Stream the image to the frontend immediately
            yield _sse("image", {"url": url, "caption": caption, "edited": False})

        except Exception as exc:
            import logging, traceback
            logging.getLogger(__name__).error(
                "Bulk slide %d failed: %s\n%s", i + 1, exc, traceback.format_exc()
            )
            yield _sse(
                "error",
                {"message": f"Slide {i + 1} failed: {safe_message(exc, action='The image request')}"},
            )
            continue

    yield _sse(
        "done",
        {
            "conversation_id": str(conversation_id),
            "cost_cents": total_cost,
            "total_images": len(generated_urls),
        },
    )


async def _ppt_turn(
    *,
    user_message: str,
    history: list[dict] | None,
    attachments: list[Attachment],
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    image_model: str | None = None,
    title_from: str | None = None,
) -> AsyncIterator[str]:
    """Three-phase PPT generation: plan → images → sandbox build.

    Long-running phases run inside an asyncio.Task while the generator
    yields SSE keepalive comments every 15 s so that no reverse-proxy
    layer (inner nginx, aaPanel HTTP/2 edge) times out the stream.
    """
    import asyncio as _asyncio

    img_size, img_quality = imaging.normalise(None, None)
    img_model = imaging.default_model(image_model)
    run_id = str(uuid.uuid4())

    yield _sse("start", {
        "conversation_id": str(conversation_id),
        "model": img_model,
        "kind": "ppt",
    })

    # Build attachment text for context
    att_text = ""
    for a in attachments:
        if a.kind != "image" and a.extracted_text:
            att_text += f"--- {a.filename} ---\n{a.extracted_text[:2000]}\n\n"

    # ------------------------------------------------------------------
    # Run ALL heavy work in a background coroutine so we can keepalive
    # ------------------------------------------------------------------
    result_box: dict = {}  # mutated by the worker; read after it finishes

    async def _ppt_worker() -> None:
        """Execute phases 1-3 and store results in *result_box*."""
        total_cost = 0
        prompt_tokens_total = 0
        completion_tokens_total = 0
        slide_count = 0
        att_id = None
        pptx_name = None
        error_msg = None
        status_events: list[dict] = []
        image_events: list[dict] = []

        # ---- Phase 1: Plan ----
        status_events.append({"message": "Planning your presentation…"})
        try:
            plan = await pptgen.generate_slide_plan(user_message, history, att_text)
        except Exception as exc:
            logger = __import__("logging").getLogger(__name__)
            logger.error("PPT plan failed: %s", exc)
            result_box.update(error=f"Could not plan the presentation: {exc}")
            return

        slide_count = len(plan)
        status_events.append({"message": f"Planned {slide_count} slides. Generating visuals…"})

        # ---- Phase 2: Images ----
        image_paths: dict[int, Path] = {}
        slides_needing = [s for s in plan if s.get("needs_image") and s.get("image_prompt")]
        total_imgs = len(slides_needing)

        for i, slide in enumerate(slides_needing):
            num = slide.get("slide_number", i + 1)
            prompt = slide["image_prompt"]
            cost = image_cost_cents(img_model, img_quality, img_size)

            status_events.append({"message": f"Generating image {i + 1} of {total_imgs} (slide {num})…"})

            try:
                async with SessionLocal() as session:
                    account = await assert_has_balance(session, user_id)
                    if account.monthly_allocation_cents and account.remaining_cents < cost:
                        status_events.append({"message": f"Skipped image for slide {num}: not enough credits."})
                        continue

                path, usage = await imaging.generate(
                    prompt, size=img_size, quality=img_quality, model=img_model
                )
                actual = image_cost_from_usage(img_model, usage)
                if actual is not None:
                    cost = actual
                total_cost += cost
                image_paths[num] = path

                url = imaging.media_url(path)

                async with SessionLocal() as session:
                    session.add(GeneratedImage(
                        user_id=user_id,
                        prompt=prompt,
                        model=img_model,
                        size=img_size,
                        quality=img_quality,
                        file_path=str(path),
                        cost_cents=cost,
                    ))
                    await _record_generated_attachment(
                        session, user_id, conversation_id, path, prompt
                    )
                    await session.commit()

                image_events.append({"url": url, "caption": f"Slide {num} visual", "edited": False})

            except Exception as exc:
                logger = __import__("logging").getLogger(__name__)
                logger.error("PPT slide %d image failed: %s", num, exc)
                status_events.append({"message": f"Image for slide {num} failed, continuing…"})

        # ---- Phase 3: Build PPTX ----
        status_events.append({"message": "Building your PowerPoint…"})
        try:
            pptx_path = await pptgen.build_presentation(plan, image_paths, run_id)
        except Exception as exc:
            logger = __import__("logging").getLogger(__name__)
            logger.error("PPT build failed: %s\n%s", exc, __import__("traceback").format_exc())
            result_box.update(error=f"Could not build the presentation: {exc}", total_cost=total_cost)
            return

        # Save as Attachment
        try:
            # Check for thumbnail generated by build_presentation
            thumb_path = pptx_path.parent / "thumbnail.png"
            thumb_str = str(thumb_path) if thumb_path.exists() else None

            async with SessionLocal() as session:
                att = Attachment(
                    user_id=user_id,
                    filename=pptx_path.name,
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    kind="pptx",
                    size_bytes=pptx_path.stat().st_size,
                    storage_path=str(pptx_path),
                    thumbnail_path=thumb_str,
                    source_type="generated",
                    conversation_id=conversation_id,
                    original_filename=f"Presentation: {user_message[:80]}",
                )
                session.add(att)
                await session.flush()
                att_id = att.id
                pptx_name = pptx_path.name
                await session.commit()
        except Exception as exc:
            logger = __import__("logging").getLogger(__name__)
            logger.error("PPT attachment save failed: %s", exc)
            result_box.update(error="Presentation built but could not be saved.", total_cost=total_cost)
            return

        # Persist assistant message + bill
        caption = f"Here is your {slide_count}-slide presentation."
        await _persist_and_bill(
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content=caption,
            image_url=None,
            kind="text",
            model=settings.text_model,
            cost_cents=total_cost,
            prompt_tokens=prompt_tokens_total,
            completion_tokens=completion_tokens_total,
            title_from=title_from,
        )

        result_box.update(
            att_id=att_id,
            pptx_name=pptx_name,
            caption=caption,
            slide_count=slide_count,
            total_cost=total_cost,
            status_events=status_events,
            image_events=image_events,
        )

    # Launch the worker as a background task
    worker = _asyncio.create_task(_ppt_worker())

    # Yield keepalive comments until the worker finishes
    while not worker.done():
        try:
            await _asyncio.wait_for(_asyncio.shield(worker), timeout=15.0)
        except _asyncio.TimeoutError:
            # SSE comment line — keeps the HTTP connection alive through
            # every proxy layer without triggering any frontend handler.
            yield ": ping\n\n"
        except Exception:
            break  # worker raised; exit keepalive loop

    # If the worker raised, retrieve it
    if worker.exception():
        logger = __import__("logging").getLogger(__name__)
        logger.error("PPT worker crashed: %s", worker.exception())
        yield _sse("error", {"message": "An internal error occurred while building your presentation."})
        yield _sse("done", {"conversation_id": str(conversation_id), "cost_cents": 0})
        return

    # ---- Flush collected events to the client ----
    if "error" in result_box:
        for ev in result_box.get("status_events", []):
            yield _sse("status", ev)
        yield _sse("error", {"message": result_box["error"]})
        yield _sse("done", {"conversation_id": str(conversation_id), "cost_cents": result_box.get("total_cost", 0)})
        return

    # Replay status + image events in order
    for ev in result_box.get("status_events", []):
        yield _sse("status", ev)
    for ev in result_box.get("image_events", []):
        yield _sse("image", ev)

    att_id = result_box["att_id"]
    pptx_name = result_box["pptx_name"]
    caption = result_box["caption"]
    slide_count = result_box["slide_count"]
    total_cost = result_box.get("total_cost", 0)

    download_url = f"/api/files/download/{att_id}"

    yield _sse("file", {"url": download_url, "filename": pptx_name, "caption": caption})
    yield _sse("done", {
        "conversation_id": str(conversation_id),
        "cost_cents": total_cost,
    })


def _data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


async def _load_attachments(
    db: AsyncSession, ids: list[uuid.UUID], user_id: uuid.UUID
) -> list[Attachment]:
    """Fetch the caller's own attachments, preserving the order they were sent."""
    if not ids:
        return []
    rows = await db.scalars(
        select(Attachment).where(Attachment.id.in_(ids), Attachment.user_id == user_id)
    )
    by_id = {r.id: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


_ATTACHMENT_RE = re.compile(r"<!-- attachments: (\[.*?\]) -->")


async def _build_history(
    db: AsyncSession, messages: list, user_id: uuid.UUID
) -> list[dict]:
    """Reconstruct conversation history with multimodal content.

    Text-only history strips out images the model previously generated or the
    user previously uploaded, so follow-up questions like "what do you see in
    that photo?" arrive blind.  This builder:

    1. Inlines assistant-generated images (``message.image_url``) as vision
       parts so the model can reference its own output on later turns.
    2. Extracts attachment IDs embedded in user messages (written at save time)
       and re-inlines any image attachments as vision parts.
    3. Strips the attachment marker comment from the text sent to the model so
       it doesn't leak JSON into the visible context.
    """
    # Collect all attachment IDs referenced across the history so we can batch-
    # load them in one query instead of N queries per message.
    all_att_ids: list[uuid.UUID] = []
    for m in messages:
        if m.role == "user":
            match = _ATTACHMENT_RE.search(m.content or "")
            if match:
                try:
                    all_att_ids.extend(uuid.UUID(i) for i in json.loads(match.group(1)))
                except (json.JSONDecodeError, ValueError):
                    pass

    # Batch-load image attachments for the whole history window.
    att_by_id: dict[uuid.UUID, Attachment] = {}
    if all_att_ids:
        rows = await db.scalars(
            select(Attachment).where(
                Attachment.id.in_(all_att_ids),
                Attachment.user_id == user_id,
                Attachment.kind == "image",
            )
        )
        att_by_id = {r.id: r for r in rows}

    history: list[dict] = []
    for m in messages:
        text = m.content or ""

        if m.role == "user":
            # Strip the attachment marker from visible text
            clean_text = _ATTACHMENT_RE.sub("", text).strip()
            match = _ATTACHMENT_RE.search(text)
            image_parts: list[dict] = []
            if match:
                try:
                    msg_att_ids = [uuid.UUID(i) for i in json.loads(match.group(1))]
                except (json.JSONDecodeError, ValueError):
                    msg_att_ids = []
                # Build vision parts from cached attachment lookup
                for aid in msg_att_ids:
                    att = att_by_id.get(aid)
                    if att:
                        p = Path(att.storage_path)
                        if p.exists():
                            image_parts.append({
                                "type": "image_url",
                                "image_url": {"url": _data_url(p, att.mime_type)},
                            })
            if image_parts:
                parts: list[dict] = [{"type": "text", "text": clean_text}]
                parts.extend(image_parts)
                history.append({"role": "user", "content": parts})
            else:
                history.append({"role": "user", "content": clean_text or text})

        elif m.role == "assistant":
            # The OpenAI API forbids image_url parts on assistant messages.
            # When the assistant generated an image, attach it as a vision part
            # to the *preceding* user message so the model can reference it on
            # subsequent turns (e.g. "make it bluer", "what do you see?").
            if m.image_url:
                img_path = imaging.path_from_url(m.image_url)
                if img_path.exists():
                    img_part = {
                        "type": "image_url",
                        "image_url": {"url": _data_url(img_path, "image/png")},
                    }
                    if history and history[-1]["role"] == "user":
                        prev = history[-1]
                        if isinstance(prev["content"], str):
                            prev["content"] = [
                                {"type": "text", "text": prev["content"]},
                                img_part,
                            ]
                        elif isinstance(prev["content"], list):
                            prev["content"].append(img_part)
            history.append({"role": "assistant", "content": text})

        else:
            history.append({"role": m.role, "content": text})

    return history


def _build_user_content(message: str, attachments: list[Attachment]):
    """Compose the user turn from the typed message plus any attachments.

    Documents are inlined as text (extracted once at upload). Images are sent
    as vision parts so the model actually looks at them rather than reading a
    filename.
    """
    if not attachments:
        return message

    documents = [a for a in attachments if a.kind != "image" and a.extracted_text]
    images = [a for a in attachments if a.kind == "image"]

    text = message
    if documents:
        blocks = [
            f"--- Attached file: {a.filename} ({a.kind.upper()}) ---\n{a.extracted_text}"
            for a in documents
        ]
        text = (
            "The user attached the following file(s). Use them to answer.\n\n"
            + "\n\n".join(blocks)
            + f"\n\n--- End of attachments ---\n\nUser message: {message}"
        )

    if not images:
        return text

    parts: list[dict] = [{"type": "text", "text": text}]
    for image in images:
        path = Path(image.storage_path)
        if path.exists():
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(path, image.mime_type)},
                }
            )
    return parts
