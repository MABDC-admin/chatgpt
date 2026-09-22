# PPT Generation Implementation Plan

## Architecture Overview

**Three-phase flow triggered inside Chat:**
1. **Plan** — LLM generates a structured slide outline (JSON) using the Smart PowerPoint master prompt
2. **Generate Images** — For slides that need visuals, generate images via sunburst/flare in parallel
3. **Assemble** — LLM generates python-pptx code with image paths injected; code executes in Docker-in-Docker sandbox; output .pptx saved as Attachment and streamed to chat

**Key decisions (confirmed with user):**
- AI generates python-pptx code → executed in Docker sandbox
- Triggered inside Chat via keyword patterns + AI intent classification
- Images generated first (sunburst/flare), then embedded into slides
- Delivered as file attachment in chat + Files page
- Docker socket mounted into backend container for DinD sandbox

---

## Task 1: Infrastructure — Docker Socket & CLI

### 1a. Mount Docker socket in docker-compose.yml
Add `/var/run/docker.sock:/var/run/docker.sock` to the backend service volumes.

**File:** `docker-compose.yml`
```yaml
    volumes:
      - mediadata:/data
      - /var/run/docker.sock:/var/run/docker.sock  # NEW: for PPT sandbox
```

### 1b. Install Docker CLI in backend Dockerfile
The backend needs the `docker` CLI to run sandbox containers. Add it to the Dockerfile.

**File:** `backend/Dockerfile`
```dockerfile
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl docker.io \
 && rm -rf /var/lib/apt/lists/*
```

Also create the PPT work directory:
```dockerfile
RUN mkdir -p /data/images /data/pptx
```

---

## Task 2: PPT Detection — Keywords + Intent Classification

### 2a. Add INTENT_PPT to ai.py
Add a new intent constant and update the classifier prompt.

**File:** `backend/app/services/ai.py`
- Add `INTENT_PPT = "PPT"` alongside existing intents
- Update `_INTENT_PROMPT` to include PPT detection:
  ```
  PPT - the user wants a PowerPoint presentation, .pptx file, or slide deck created.
  ```
- Add examples:
  ```
  "make me a ppt about photosynthesis" -> PPT
  "create a 10-slide presentation on WW2" -> PPT
  "generate a powerpoint for my lesson" -> PPT
  "what is a ppt?" -> TEXT
  ```

### 2b. Add keyword pre-filter in chat.py (like bulk images)
Before intent classification, check for PPT keywords to short-circuit.

**File:** `backend/app/routers/chat.py`
```python
_PPT_PATTERNS = [
    r"(?:make|create|generate|build)\s+(?:a\s+)?(?:ppt|pptx|powerpoint|presentation|slide\s*deck)",
    r"(?:ppt|pptx|powerpoint|presentation|slide\s*deck)\s+(?:about|on|for|of)",
    r"\d+\s*(?:slides?)",
]
msg_lower = payload.message.lower()
is_ppt_request = any(re.search(p, msg_lower) for p in _PPT_PATTERNS)
```

If `is_ppt_request`, skip normal intent classification and route directly to `_ppt_turn()`.

---

## Task 3: PPT Master Prompt Injection

### 3a. Create PPT system prompt file
Save the user's master guide as a separate prompt file that gets appended to the system prompt ONLY when generating PPT code/plans.

**File:** `backend/app/prompts/ppt_master_prompt.txt`
- Contains the full 37-point Smart PowerPoint Generation System Prompt provided by the user

### 3b. Load it in ai.py
**File:** `backend/app/services/ai.py`
```python
_PPT_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "ppt_master_prompt.txt"

def _load_ppt_prompt() -> str:
    try:
        return _PPT_PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

PPT_MASTER_PROMPT = _load_ppt_prompt()
```

---

## Task 4: Phase 1 — Slide Outline Generation

### 4a. Create `app/services/pptgen.py` service module
This handles all PPT-specific logic, keeping chat.py clean.

**File:** `backend/app/services/pptgen.py` (NEW)

```python
async def generate_slide_plan(
    user_message: str,
    history: list[dict],
    attachments_text: str,
) -> list[dict]:
    """
    Call the LLM with the PPT master prompt to produce a structured slide plan.
    Returns a list of dicts:
    [
      {
        "slide_number": 1,
        "title": "...",
        "layout": "title|content|two_column|image|quiz|summary",
        "bullets": ["...", "..."],
        "speaker_notes": "...",
        "needs_image": true/false,
        "image_prompt": "..."  # only if needs_image=true
      },
      ...
    ]
    """
```

The LLM call uses the text model with the PPT master prompt as system message, instructing it to output ONLY valid JSON (an array of slide objects). Use `response_format={"type": "json_object"}` or parse from fenced code block.

---

## Task 5: Phase 2 — Image Generation for Slides

### 5a. Generate images in parallel for slides that need them
**File:** `backend/app/services/pptgen.py`

```python
async def generate_slide_images(
    plan: list[dict],
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    img_model: str,
    img_quality: str,
    img_size: str,
    progress_callback: Callable[[int, int, str], None],  # (current, total, status_msg)
) -> dict[int, Path]:
    """
    For each slide where needs_image=True, generate an image via imaging.generate().
    Returns {slide_number: image_path} mapping.
    Streams progress via callback.
    """
```

Uses existing `imaging.generate()` function. Each generated image is also recorded via `_record_generated_attachment()` and `GeneratedImage` table (reusing existing pattern).

---

## Task 6: Phase 3 — Python-pptx Code Generation & Sandbox Execution

### 6a. Generate python-pptx code via LLM
**File:** `backend/app/services/pptgen.py`

```python
async def generate_pptx_code(
    plan: list[dict],
    image_paths: dict[int, Path],
    output_path: str,
) -> str:
    """
    Call the LLM with the PPT master prompt + slide plan + image paths.
    Instruct it to write python-pptx code that:
    - Creates a 16:9 presentation
    - Uses consistent fonts, colors, layout variety per the master guide
    - Embeds images at the provided paths
    - Saves to the specified output_path
    Returns the Python source code as a string.
    """
```

The prompt injects:
- The PPT master prompt (design guidelines)
- The structured slide plan from Phase 1
- A mapping of slide numbers to actual image file paths
- Instructions: "Write ONLY Python code using python-pptx. No markdown fences. The code must save the presentation to `{output_path}`."

### 6b. Execute code in Docker-in-Docker sandbox
**File:** `backend/app/services/pptgen.py`

```python
import subprocess
import tempfile
from pathlib import Path

SANDBOX_IMAGE = "python:3.12-slim"
TIMEOUT_SECONDS = 120

async def execute_pptx_code(code: str, image_paths: list[Path], output_dir: Path) -> Path:
    """
    1. Write the python-pptx code to a temp .py file in output_dir
    2. Run `docker run --rm --network none -v output_dir:/work python:3.12-slim 
       sh -c 'pip install python-pptx -q && python /work/generate.py'`
    3. Return the path to the generated .pptx file
    """
    script_path = output_dir / "generate.py"
    script_path.write_text(code)
    
    cmd = [
        "docker", "run", "--rm",
        "--network", "none",           # No internet access
        "--memory", "512m",            # Memory limit
        "--cpus", "1",                 # CPU limit
        "-v", f"{output_dir}:/work",   # Only mount the work dir
        SANDBOX_IMAGE,
        "sh", "-c", "pip install python-pptx -q && python /work/generate.py"
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SECONDS)
    
    if proc.returncode != 0:
        raise RuntimeError(f"Sandbox execution failed: {stderr.decode()}")
    
    # Find the generated .pptx
    pptx_files = list(output_dir.glob("*.pptx"))
    if not pptx_files:
        raise RuntimeError("No .pptx file was generated")
    return pptx_files[0]
```

---

## Task 7: Streaming Turn Handler in chat.py

### 7a. Add `_ppt_turn()` async generator
**File:** `backend/app/routers/chat.py`

Follows the same pattern as `_bulk_image_turn()`. Yields SSE events:
- `start` — kind: "ppt"
- `status` — "Planning slides…", "Generating images (3/10)…", "Building presentation…"
- `image` — each generated slide image (so user sees progress)
- `file` — NEW event type: `{url, filename, caption}` for the final .pptx download
- `error` — if anything fails
- `done` — cost summary

```python
async def _ppt_turn(
    *,
    user_message: str,
    history: list[dict],
    attachments: list[Attachment],
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    image_model: str | None,
    title_from: str | None = None,
) -> AsyncIterator[str]:
    yield _sse("start", {"conversation_id": str(conversation_id), "kind": "ppt"})
    
    # Phase 1: Plan
    yield _sse("status", {"message": "Planning your presentation…"})
    plan = await pptgen.generate_slide_plan(user_message, history, ...)
    
    # Phase 2: Images
    img_model = imaging.default_model(image_model)
    image_paths = {}
    slides_needing_images = [s for s in plan if s.get("needs_image")]
    total_images = len(slides_needing_images)
    
    for i, slide in enumerate(slides_needing_images):
        yield _sse("status", {"message": f"Generating image {i+1} of {total_images}…"})
        path, usage = await imaging.generate(slide["image_prompt"], ...)
        image_paths[slide["slide_number"]] = path
        url = imaging.media_url(path)
        yield _sse("image", {"url": url, "caption": f"Slide {slide['slide_number']} visual", "edited": False})
        # Record attachment + GeneratedImage (existing pattern)
        ...
    
    # Phase 3: Code gen + sandbox
    yield _sse("status", {"message": "Building your PowerPoint…"})
    output_dir = Path(f"/data/pptx/{uuid.uuid4()}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "presentation.pptx"
    
    code = await pptgen.generate_pptx_code(plan, image_paths, str(output_path))
    pptx_path = await pptgen.execute_pptx_code(code, list(image_paths.values()), output_dir)
    
    # Save as Attachment
    async with SessionLocal() as session:
        att = Attachment(
            user_id=user_id,
            filename=pptx_path.name,
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            kind="document",
            size_bytes=pptx_path.stat().st_size,
            storage_path=str(pptx_path),
            source_type="generated",
            conversation_id=conversation_id,
            original_filename=f"Presentation: {user_message[:80]}",
        )
        session.add(att)
        await session.commit()
    
    download_url = f"/api/files/download/{att.id}"
    yield _sse("file", {"url": download_url, "filename": pptx_path.name, "caption": "Here is your PowerPoint presentation."})
    
    # Persist assistant message
    await _persist_and_bill(...)
    
    yield _sse("done", {"conversation_id": str(conversation_id), "cost_cents": total_cost})
```

### 7b. Wire PPT routing into the main `chat()` endpoint
After bulk image detection, before normal intent classification:
```python
if is_ppt_request:
    # persist user message
    ...
    return StreamingResponse(
        _ppt_turn(...),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

---

## Task 8: Frontend — Handle `file` SSE Event

### 8a. Add `onFile` handler to stream.ts
**File:** `frontend/lib/stream.ts`

Add to `StreamHandlers` type:
```typescript
onFile?: (data: { url: string; filename: string; caption: string }) => void;
```

Add to event parsing loop:
```typescript
else if (event === "file") handlers.onFile?.(payload);
```

### 8b. Handle `file` event in chat/page.tsx
**File:** `frontend/app/chat/page.tsx`

Add `onFile` handler that renders a downloadable file card in the chat stream:
```typescript
onFile: ({ url, filename, caption }) =>
  setMessages((prev) => {
    const next = [...prev];
    next.push({
      role: "assistant",
      content: caption,
      file_url: url,
      file_name: filename,
    });
    return next;
  }),
```

### 8c. Extend ChatMessage type
**File:** `frontend/lib/api.ts`

Add optional fields to `ChatMessage`:
```typescript
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  image_url?: string;
  file_url?: string;    // NEW
  file_name?: string;   // NEW
  pending?: boolean;
};
```

### 8d. Render file attachment card in chat messages
In the message rendering loop in `chat/page.tsx`, add a file download card after the image handling:
```tsx
{m.file_url && (
  <div className="mt-3 flex items-center gap-3 rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 max-w-md">
    <FileText className="h-8 w-8 text-emerald-600 shrink-0" />
    <div className="flex-1 min-w-0">
      <p className="text-sm font-medium text-[#1f2937] truncate">{m.file_name || "Download"}</p>
      <p className="text-xs text-[#6b7280]">PowerPoint Presentation</p>
    </div>
    <a
      href={m.file_url.startsWith("/api/") ? `${API_BASE}${m.file_url}` : m.file_url}
      download
      className="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-full bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
      title="Download"
    >
      <ArrowUp className="h-4 w-4 rotate-180" />
    </a>
  </div>
)}
```

---

## Task 9: Credit Billing

### 9a. Calculate total PPT cost
PPT generation costs = sum of all image generation costs + text model costs for planning and code generation.

Track text token usage from both LLM calls (plan + code gen) and bill them as text turns. Image costs are billed individually as they're generated (same as bulk images).

---

## Task 10: Error Handling & Edge Cases

- **Sandbox timeout**: Kill container after 120s, report error to user
- **Code generation failure**: If LLM produces invalid Python, retry once with error feedback
- **Image generation failure**: Skip that slide's image, continue with placeholder shape
- **Credit exhaustion mid-generation**: Stop, deliver partial result (images generated so far + text-only PPT)
- **Empty plan**: If LLM returns no slides, fall back to text response explaining the issue

---

## File Change Summary

| File | Action |
|------|--------|
| `docker-compose.yml` | Edit: add docker socket volume |
| `backend/Dockerfile` | Edit: add docker.io, /data/pptx |
| `backend/app/services/ai.py` | Edit: add INTENT_PPT, load PPT prompt |
| `backend/app/services/pptgen.py` | **Create**: plan, codegen, sandbox execution |
| `backend/app/prompts/ppt_master_prompt.txt` | **Create**: user's 37-point guide |
| `backend/app/routers/chat.py` | Edit: PPT detection, routing, `_ppt_turn()` |
| `frontend/lib/stream.ts` | Edit: add `onFile` handler |
| `frontend/lib/api.ts` | Edit: extend ChatMessage type |
| `frontend/app/chat/page.tsx` | Edit: handle file events, render download card |

## Deployment Steps
1. Rebuild backend with `--no-cache` (Dockerfile changed)
2. Rebuild frontend with `--no-cache`
3. `docker compose up -d` to apply docker-compose.yml changes (socket mount)
4. Verify all containers healthy
