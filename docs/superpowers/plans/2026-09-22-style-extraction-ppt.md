# Style Extraction PPT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current 5 color-only themes with 6 rich visual styles extracted from SlidesCarnival education templates, auto-matched by subject, so generated PPTs look professionally designed.

**Architecture:** A curated `STYLES` catalog in `pptgen.py` replaces `THEMES`, adding fonts, layout preferences, and decorative hints per style. The backend auto-matches the lesson subject to a style keyword. Frontend shows style cards with preview swatches instead of bare color tiles. An "Auto" option lets the system pick.

**Tech Stack:** python-pptx, FastAPI, Next.js/React, TypeScript

**Spec:** Brainstorming design approved 2026-09-22 — style extraction approach with auto-match by subject.

## Global Constraints

- All styles must include the full palette keys currently used by renderers: primary, secondary, accent, dark_text, light_text, bg_white, bg_light, bg_quiz, bg_summary, dark_bg
- Font names must be web-safe or bundled; fall back to Calibri if unavailable on server
- Subject matching is case-insensitive fuzzy match against keyword lists
- The "emerald" theme name is retired; default style is "auto" which resolves at build time
- Slide dimensions remain 13.33 x 7.5 inches (16:9)
- No new dependencies beyond python-pptx already installed

## Review Focus

1. Unknown subject (e.g., "basketball") that matches no keywords → must gracefully fall back to a default style, not crash
2. Teacher manually picks a style while auto-match also fires → manual pick must win
3. Font name from SlidesCarnival template doesn't exist on server → must fall back to Calibri without error
4. Style with missing palette key (e.g., no bg_quiz) → renderer must have safe defaults
5. PPT mode toggle off but pptTheme state still set → must not affect non-PPT image generation

---

### Task 1: Build Curated STYLES Catalog

**Files:**
- Modify: `backend/app/services/pptgen.py:48-82` (replace THEMES dict)

**Interfaces:**
- Consumes: Nothing (standalone data)
- Produces: `STYLES: dict[str, dict]` with 6 entries, each containing colors, fonts, layout_prefs, rounded, and keywords

Extraction spike produced raw palettes from 3 SlidesCarnival templates. The frequency-based heuristic misassigns semantic roles (e.g., maps background pink as "primary"). This task hand-curates 6 styles with correct semantic color mapping.

- [ ] **Step 1: Replace THEMES dict with STYLES dict in pptgen.py**

Replace the existing `THEMES` dict (lines ~48-82) with:

```python
STYLES: dict[str, dict] = {
    "playful_primary": {
        "keywords": ["physical education", "pe", "sports", "health", "fitness", "gym", "dance", "athletics"],
        "colors": {
            "primary": "09B2AC", "secondary": "267B78", "accent": "FF828B",
            "dark_text": "1a1a2e", "light_text": "ffffff",
            "bg_white": "FFF1EC", "bg_light": "fce4ec", "bg_quiz": "FFE17E",
            "bg_summary": "8FD4D1", "dark_bg": "1a1a2e",
        },
        "fonts": {"title": "Calibri", "body": "Calibri", "title_size": 36, "body_size": 22},
        "layout_prefs": ["hook", "two_column", "activity", "image"],
        "rounded": False,
    },
    "illustrated_science": {
        "keywords": ["science", "biology", "chemistry", "physics", "nature", "environment", "ecology", "anatomy"],
        "colors": {
            "primary": "1C0072", "secondary": "E5645E", "accent": "F9705A",
            "dark_text": "1C0072", "light_text": "ffffff",
            "bg_white": "F5F3E5", "bg_light": "F8EACD", "bg_quiz": "FE6868",
            "bg_summary": "FCC7C0", "dark_bg": "1C0072",
        },
        "fonts": {"title": "Calibri", "body": "Calibri", "title_size": 36, "body_size": 20},
        "layout_prefs": ["hook", "objectives", "content", "image", "quiz"],
        "rounded": False,
    },
    "playful_math": {
        "keywords": ["math", "mathematics", "numbers", "counting", "arithmetic", "algebra", "geometry", "calculus", "statistics"],
        "colors": {
            "primary": "6DCCD4", "secondary": "FFA49C", "accent": "FFE17E",
            "dark_text": "1a1a2e", "light_text": "ffffff",
            "bg_white": "FFF5E2", "bg_light": "FAC2BD", "bg_quiz": "FFE17E",
            "bg_summary": "6DCCD4", "dark_bg": "1a1a2e",
        },
        "fonts": {"title": "Calibri", "body": "Calibri", "title_size": 34, "body_size": 22},
        "layout_prefs": ["two_column", "content", "quiz", "activity"],
        "rounded": False,
    },
    "warm_history": {
        "keywords": ["history", "social studies", "geography", "culture", "politics", "civilization", "ancient", "war", "revolution"],
        "colors": {
            "primary": "8B4513", "secondary": "D2691E", "accent": "DAA520",
            "dark_text": "3e2723", "light_text": "ffffff",
            "bg_white": "FFF8E7", "bg_light": "FAEBD7", "bg_quiz": "F4A460",
            "bg_summary": "DEB887", "dark_bg": "3e2723",
        },
        "fonts": {"title": "Georgia", "body": "Calibri", "title_size": 36, "body_size": 20},
        "layout_prefs": ["hook", "content", "quote", "two_column", "summary"],
        "rounded": False,
    },
    "fresh_language": {
        "keywords": ["english", "language", "reading", "writing", "literature", "grammar", "vocabulary", "essay", "poetry", "filipino", "spanish"],
        "colors": {
            "primary": "2d6a4f", "secondary": "40916c", "accent": "f4a261",
            "dark_text": "1b4332", "light_text": "ffffff",
            "bg_white": "fefae0", "bg_light": "faedcd", "bg_quiz": "f4a261",
            "bg_summary": "d8f3dc", "dark_bg": "1b4332",
        },
        "fonts": {"title": "Calibri", "body": "Calibri", "title_size": 36, "body_size": 22},
        "layout_prefs": ["hook", "objectives", "content", "quote", "activity"],
        "rounded": True,
    },
    "minimal_tech": {
        "keywords": ["technology", "computer", "coding", "programming", "ict", "digital", "engineering", "robotics", "ai"],
        "colors": {
            "primary": "2563eb", "secondary": "1e40af", "accent": "06b6d4",
            "dark_text": "0f172a", "light_text": "ffffff",
            "bg_white": "f8fafc", "bg_light": "e2e8f0", "bg_quiz": "bae6fd",
            "bg_summary": "bfdbfe", "dark_bg": "0f172a",
        },
        "fonts": {"title": "Calibri", "body": "Calibri", "title_size": 34, "body_size": 20},
        "layout_prefs": ["content", "two_column", "image", "quiz"],
        "rounded": True,
    },
}

# Backward compat alias — default fallback when no style matches
PALETTE = STYLES["playful_primary"]["colors"]
```

Note: Font names are all set to Calibri/Georgia (web-safe) because SlidesCarnival fonts like "DM Sans", "Lilita One", "Exo 2" are NOT installed on the server. The extraction gave us sizes (34-36pt titles, 20-24pt body) which ARE used.

- [ ] **Step 2: Remove old THEMES dict entirely**

Delete the old `THEMES` dict that had emerald/ocean/sunset/lavender/minimal. It's fully replaced by `STYLES`.

- [ ] **Step 3: Verify file parses correctly**

Run: `python3 -c "from app.services.pptgen import STYLES; print(len(STYLES))"`
Expected: `6`

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/pptgen.py
git commit -m "feat: replace THEMES with curated STYLES catalog from SlidesCarnival extraction"
```

---

### Task 2: Add Subject Auto-Matching Function

**Files:**
- Modify: `backend/app/services/pptgen.py` (add after STYLES dict)

**Interfaces:**
- Consumes: `STYLES` dict from Task 1
- Produces: `def match_style(topic: str, subject: str, manual_style: str | None) -> tuple[str, dict]` returning (style_id, colors_dict)

- [ ] **Step 1: Write test for auto-matching**

Create `backend/tests/test_style_match.py`:

```python
import pytest
from app.services.pptgen import match_style

def test_exact_subject_match():
    style_id, colors = match_style("", "Science", None)
    assert style_id == "illustrated_science"
    assert "primary" in colors

def test_topic_keyword_match():
    style_id, _ = match_style("Learning about fractions", "", None)
    assert style_id == "playful_math"

def test_manual_override_wins():
    style_id, _ = match_style("Science topic", "Science", "warm_history")
    assert style_id == "warm_history"

def test_no_match_returns_default():
    style_id, colors = match_style("basketball tricks", "", None)
    assert style_id == "playful_primary"
    assert "primary" in colors

def test_case_insensitive():
    style_id, _ = match_style("", "PHYSICS", None)
    assert style_id == "illustrated_science"

def test_partial_keyword_match():
    style_id, _ = match_style("Ancient Rome civilization", "", None)
    assert style_id == "warm_history"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_style_match.py -v`
Expected: ImportError or AttributeError (match_style doesn't exist yet)

- [ ] **Step 3: Implement match_style function**

Add to `pptgen.py` after the STYLES dict:

```python
def match_style(topic: str, subject: str, manual_style: str | None = None) -> tuple[str, dict]:
    """Pick the best visual style based on lesson subject/topic.
    
    Manual selection always wins. Otherwise fuzzy-match keywords.
    Returns (style_id, colors_dict).
    """
    # Manual override always wins
    if manual_style and manual_style in STYLES:
        return manual_style, STYLES[manual_style]["colors"]
    
    # Combine topic + subject into searchable text
    haystack = f"{topic} {subject}".lower()
    
    best_match = None
    best_score = 0
    
    for style_id, style_data in STYLES.items():
        score = 0
        for kw in style_data.get("keywords", []):
            if kw.lower() in haystack:
                # Longer keyword matches score higher (more specific)
                score += len(kw)
        if score > best_score:
            best_score = score
            best_match = style_id
    
    # Fallback to first style if nothing matched
    if best_match is None:
        best_match = "playful_primary"
    
    return best_match, STYLES[best_match]["colors"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_style_match.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pptgen.py backend/tests/test_style_match.py
git commit -m "feat: add subject-to-style auto-matching with manual override"
```

---

### Task 3: Thread Style Through Renderers and Build Pipeline

**Files:**
- Modify: `backend/app/services/pptgen.py` (all `_render_*` functions, `_build_deck`, `build_presentation`)
- Modify: `backend/app/routers/chat.py` (lines ~950-960 where `_ppt_turn` parses theme, and line ~1061 where `build_presentation` is called)

**Interfaces:**
- Consumes: `match_style()` from Task 2, `STYLES` from Task 1
- Produces: Updated `_build_deck(plan, image_paths, output_path, style_id)` that passes full style dict (not just colors) to renderers

Currently every renderer accepts `t: dict` which is just the colors palette. We need to also pass font info and layout prefs. The cleanest approach: change `t` from colors-only to the full style dict, and update renderers to read `t["colors"]["primary"]` etc.

- [ ] **Step 1: Update _build_deck signature**

Change:
```python
def _build_deck(plan: list[dict], image_paths: dict[int, Path], output_path: Path, theme_name: str = "emerald") -> None:
    t = THEMES.get(theme_name, PALETTE)
```
To:
```python
def _build_deck(plan: list[dict], image_paths: dict[int, Path], output_path: Path, style_id: str = "playful_primary") -> None:
    style = STYLES.get(style_id, STYLES["playful_primary"])
    t = style["colors"]
    fonts = style.get("fonts", {"title": "Calibri", "body": "Calibri", "title_size": 36, "body_size": 22})
```

- [ ] **Step 2: Update all renderers to use font sizes from style**

In every `_render_*` function, replace hardcoded font sizes with style-derived values. For example in `_render_title`:

Change:
```python
size=48, bold=True, hex_color=t["light_text"], align="center",
```
To:
```python
size=fonts.get("title_size", 36) + 12, bold=True, hex_color=t["light_text"], align="center",
```

And in `_render_content` title bar:
```python
size=fonts.get("title_size", 36), bold=True, hex_color=t["light_text"],
```

And bullet text:
```python
p.font.size = Pt(fonts.get("body_size", 22))
p.font.name = fonts.get("body", "Calibri")
```

Update `_add_bullets` helper to accept font params:
```python
def _add_bullets(slide, left, top, width, height, bullets: list[str], hex_color: str = "1f2937", font_name: str = "Calibri", font_size: int = 22):
    ...
    p.font.size = Pt(font_size)
    p.font.name = font_name
```

Then every renderer calls it with:
```python
_add_bullets(..., font_name=fonts.get("body", "Calibri"), font_size=fonts.get("body_size", 22))
```

- [ ] **Step 3: Pass fonts through _build_deck to each renderer**

Change the renderer call pattern in `_build_deck`:
```python
if layout == "image":
    _render_image(deck, spec, t, image_paths.get(spec.get("slide_number", i + 1)), fonts)
else:
    renderer = _LAYOUTS.get(layout, _render_content)
    renderer(deck, spec, t, fonts)
```

Update ALL renderer signatures from `(deck, spec, t)` to `(deck, spec, t, fonts)`.

- [ ] **Step 4: Update build_presentation async wrapper**

Change:
```python
async def build_presentation(plan, image_paths, run_id, theme="emerald") -> Path:
    await asyncio.to_thread(_build_deck, plan, image_paths, output_path, theme)
```
To:
```python
async def build_presentation(plan, image_paths, run_id, style_id="playful_primary") -> Path:
    await asyncio.to_thread(_build_deck, plan, image_paths, output_path, style_id)
```

- [ ] **Step 5: Update chat.py _ppt_turn to use match_style**

In `_ppt_turn` (around line 950-960), replace the theme regex parsing with:

```python
# Parse manual style from TEACHERDECK structured input if present
import re as _re
style_match = _re.search(r"Style:\s*(\w+)", user_message, _re.IGNORECASE)
manual_style = style_match.group(1).lower() if style_match else None

# Extract topic and subject for auto-matching
topic_match = _re.search(r"Topic:\s*(.+)", user_message, _re.IGNORECASE)
subject_match = _re.search(r"Subject:\s*(.+)", user_message, _re.IGNORECASE)
topic = topic_match.group(1).strip() if topic_match else user_message[:200]
subject = subject_match.group(1).strip() if subject_match else ""

style_id, _ = pptgen.match_style(topic, subject, manual_style)
```

Then at line ~1061, change:
```python
pptx_path = await pptgen.build_presentation(plan, image_paths, run_id, theme)
```
To:
```python
pptx_path = await pptgen.build_presentation(plan, image_paths, run_id, style_id)
```

Remove the old `theme` parameter and `theme_match` regex.

- [ ] **Step 6: Run existing tests**

Run: `pytest backend/tests/ -v -k ppt`
Expected: All existing PPT tests pass (if any)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/pptgen.py backend/app/routers/chat.py
git commit -m "feat: thread style fonts and auto-match through PPT build pipeline"
```

---

### Task 4: Update Frontend API Constants

**Files:**
- Modify: `frontend/lib/api.ts` (replace PPT_THEMES)

**Interfaces:**
- Consumes: Nothing
- Produces: `PPT_STYLES` constant array, `PptStyle` type

- [ ] **Step 1: Replace PPT_THEMES with PPT_STYLES in api.ts**

Change the existing `PPT_THEMES` block to:

```typescript
export type PptStyle = {
  id: string;
  label: string;
  emoji: string;
  primary: string;
  accent: string;
  bg: string;
  description: string;
};

export const PPT_STYLES: PptStyle[] = [
  { id: "auto", label: "Auto Match", emoji: "🎯", primary: "#6b7280", accent: "#9ca3af", bg: "#f9fafb", description: "System picks the best style for your subject" },
  { id: "playful_primary", label: "Playful PE", emoji: "🏃", primary: "#09B2AC", accent: "#FF828B", bg: "#FFF1EC", description: "Teal & coral, great for sports and health" },
  { id: "illustrated_science", label: "Science Lab", emoji: "🔬", primary: "#1C0072", accent: "#F9705A", bg: "#F5F3E5", description: "Deep indigo & warm accents for STEM" },
  { id: "playful_math", label: "Math Fun", emoji: "🔢", primary: "#6DCCD4", accent: "#FFE17E", bg: "#FFF5E2", description: "Bright pastels for numbers and logic" },
  { id: "warm_history", label: "History Warm", emoji: "📜", primary: "#8B4513", accent: "#DAA520", bg: "#FFF8E7", description: "Earthy tones for social studies" },
  { id: "fresh_language", label: "Language Arts", emoji: "📖", primary: "#2d6a4f", accent: "#f4a261", bg: "#fefae0", description: "Forest greens for reading and writing" },
  { id: "minimal_tech", label: "Tech Clean", emoji: "💻", primary: "#2563eb", accent: "#06b6d4", bg: "#f8fafc", description: "Modern blues for technology topics" },
];
```

Remove the old `PPT_THEMES` and `PptTheme` exports.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors related to PPT_STYLES

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/api.ts
git commit -m "feat: replace PPT_THEMES with richer PPT_STYLES catalog"
```

---

### Task 5: Frontend Style Picker UI

**Files:**
- Modify: `frontend/app/chat/page.tsx` (imports, state, send function, TeacherDeck bar)

**Interfaces:**
- Consumes: `PPT_STYLES` from Task 4
- Produces: Updated TeacherDeck bar with style card picker

- [ ] **Step 1: Update import**

Change `PPT_THEMES` to `PPT_STYLES` in the import statement at the top of page.tsx.

- [ ] **Step 2: Rename state variable**

Change:
```typescript
const [pptTheme, setPptTheme] = useState("emerald");
```
To:
```typescript
const [pptStyle, setPptStyle] = useState("auto");
```

- [ ] **Step 3: Update send() formatting**

Change the Theme line in the TEACHERDECK message builder:
```typescript
if (pptStyle && pptStyle !== "auto") parts.push(`Style: ${pptStyle}`);
```

- [ ] **Step 4: Replace theme tile buttons with style cards**

Find the theme picker div in the TeacherDeck bar (the one with `PPT_THEMES.map((th) => ...)`). Replace the entire block:

```tsx
<div className="flex items-center gap-1 ml-auto overflow-x-auto max-w-[60%] scrollbar-hide">
  <span className="text-xs text-amber-600 mr-1 shrink-0">Style:</span>
  {PPT_STYLES.map((st) => {
    const active = pptStyle === st.id;
    return (
      <button
        key={st.id}
        type="button"
        onClick={() => setPptStyle(st.id)}
        disabled={streaming}
        title={st.description}
        className={cn(
          "shrink-0 flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition-all disabled:opacity-50",
          active
            ? "border-amber-400 bg-amber-50 text-amber-800 ring-1 ring-amber-300 shadow-sm"
            : "border-gray-200 bg-white text-gray-600 hover:border-amber-200 hover:bg-amber-50/30"
        )}
      >
        <span className="text-sm">{st.emoji}</span>
        <span className="flex gap-0.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: st.primary }} />
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: st.accent }} />
        </span>
        <span className="hidden md:inline whitespace-nowrap">{st.label}</span>
      </button>
    );
  })}
</div>
```

This renders as: `[🎯 ●● Auto] [🏃 ●● Playful PE] [🔬 ●● Science Lab] ...` where ●● are colored dots showing the palette.

- [ ] **Step 5: Verify frontend builds**

Run: `cd frontend && npm run build 2>&1 | tail -20`
Expected: Build succeeds with no errors

- [ ] **Step 6: Commit**

```bash
git add frontend/app/chat/page.tsx
git commit -m "feat: style card picker with auto-match and emoji previews"
```

---

### Task 6: Deploy and End-to-End Verification

**Files:**
- No code changes — deployment only

- [ ] **Step 1: SCP all changed files to server**

```bash
scp ... pptgen.py root@10.121.15.125:/opt/teacherai-school/backend/app/services/pptgen.py
scp ... chat.py root@10.121.15.125:/opt/teacherai-school/backend/app/routers/chat.py
scp ... api.ts root@10.121.15.125:/opt/teacherai-school/frontend/lib/api.ts
scp ... page.tsx root@10.121.15.125:/opt/teacherai-school/frontend/app/chat/page.tsx
```

- [ ] **Step 2: Rebuild containers**

```bash
ssh ... 'cd /opt/teacherai-school && docker compose up -d --build frontend backend'
```

- [ ] **Step 3: Verify deployment**

```bash
ssh ... 'grep -c "STYLES" /opt/teacherai-school/backend/app/services/pptgen.py'
# Expected: >0
ssh ... 'grep -c "match_style" /opt/teacherai-school/backend/app/services/pptgen.py'
# Expected: >0
ssh ... 'grep -c "PPT_STYLES" /opt/teacherai-school/frontend/lib/api.ts'
# Expected: >0
```

- [ ] **Step 4: Test auto-match via API**

```bash
ssh ... 'docker exec teacherai-school-backend-1 python3 -c "
from app.services.pptgen import match_style
print(match_style(\"Photosynthesis\", \"Science\", None)[0])
print(match_style(\"Fractions\", \"Math\", None)[0])
print(match_style(\"World War 2\", \"History\", None)[0])
print(match_style(\"Basketball\", \"PE\", None)[0])
print(match_style(\"Random topic\", \"\", None)[0])
print(match_style(\"Anything\", \"\", \"minimal_tech\")[0])
"'
```
Expected output:
```
illustrated_science
playful_math
warm_history
playful_primary
playful_primary
minimal_tech
```

- [ ] **Step 5: Generate a PPT end-to-end via chat.mabdc.com**

Manually test: Go to chat.mabdc.com, toggle PPT mode, type "Topic: Photosynthesis, Subject: Science, Level: Grade 7, Slides: 10", generate. Verify:
1. Style auto-selects "Science Lab" (indigo/coral palette)
2. Images match slide content (from previous fix)
3. Download works (from attachment fix)
4. Fonts and colors differ from the old emerald default

- [ ] **Step 6: Commit deployment verification notes**

No code commit needed — this is operational verification.
