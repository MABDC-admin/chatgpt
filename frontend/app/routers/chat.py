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
from app.services import ai, imaging
from app.services.credits import assert_has_balance, record_usage
from app.services.errors import safe_message
from app.services.embeddings import search_similar
from app.services.pricing import image_cost_cents, image_cost_from_usage, text_cost_cents
from app.services.prompt_builder import expand_image_prompt, parse_slides_outline
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/api", tags=["chat"])

MAX_CONTEXT_MESSAGES = 20


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
    # Bulk slide generation: detect BEFORE intent classification so that
    # phrases like "bulk image generation 10 images" are not swallowed
    # by the text-model path.
    # ------------------------------------------------------------------
    import re
    _BULK_PATTERNS = [
        r"build\s+all\s+slides", r"generate\s+all\s+slides", r"create\s+all\s+slides",
        r"make\s+all\s+slides", r"all\s+slides\s+as\s+images", r"each\s+slide\s+as\s+image",
        r"turn\s+each\s+slide", r"slide\s+by\s+slide", r"one\s+image\s+per\s+slide",
        r"bulk\s+(?:image|slide)s?", r"(?:generate|create|make)\s+\d+\s+(?:slides|images)",
        r"\d+\s+(?:slides|images)",
    ]
    msg_lower = payload.message.lower()
    is_bulk_request = any(re.search(p, msg_lower) for p in _BULK_PATTERNS)

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

    # Record in the gallery table too, so every picture -- however it was made --
    # shows up in one place rather than only inside its own thread.
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

            # Record in gallery
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
