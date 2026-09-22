"""Render generated Markdown into downloadable DOCX, PDF and PPTX files.

The teacher tools stream Markdown. Teachers need something they can print,
hand out, or drop into a staff folder, so this turns that Markdown into a
real document rather than asking them to copy-paste into Word.

Everything is produced in memory and returned as bytes; nothing is written
to disk, so there is no cleanup or storage growth to manage.
"""

import io
import re

# GFM subset our planner actually emits.
_H1 = re.compile(r"^#\s+(.*)$")
_H2 = re.compile(r"^##\s+(.*)$")
_H3 = re.compile(r"^###\s+(.*)$")
_H4 = re.compile(r"^####\s+(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
_BLOCKQUOTE = re.compile(r"^>\s?(.*)$")
_RULE = re.compile(r"^-{3,}$|^_{3,}$|^\*{3,}$")
_FENCE = re.compile(r"^```\s*(\S*)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_CODE_INLINE = re.compile(r"`([^`\n]+)`")


def _strip_marks(text: str) -> str:
    """Remove inline markdown so plain-text renderers do not show the syntax."""
    text = _CODE_INLINE.sub(r"\1", text)
    return _ITALIC.sub(r"\1", _BOLD.sub(r"\1", text))


def _split_row(line: str) -> list[str]:
    """Split a `| a | b | c |` row into its cells, handling backslash escapes."""
    # Drop leading/trailing pipe then split on unescaped pipes.
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    parts = re.split(r"(?<!\\)\|", stripped)
    return [p.replace(r"\|", "|").strip() for p in parts]


class Block:
    __slots__ = ("kind", "text", "extra")

    def __init__(self, kind: str, text: str = "", extra=None):
        self.kind = kind
        self.text = text
        # For "table": {"headers": [...], "rows": [[...], ...]}
        # For "code":  {"lang": "python"}
        self.extra = extra


def parse(markdown: str) -> list[Block]:
    """Flatten Markdown into a list of typed blocks the renderers can walk.

    Supports GFM tables, fenced code blocks, block-quotes and all six heading
    levels (though only H1-H4 render distinctly). Unknown lines fall through
    to plain body text so the pipeline never drops content on the floor.
    """
    blocks: list[Block] = []
    lines = markdown.splitlines()
    i = 0
    in_code = False
    code_buffer: list[str] = []
    code_lang = ""

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        # Fenced code block: capture verbatim until the closing fence.
        if _FENCE.match(line):
            m = _FENCE.match(line)
            if not in_code:
                in_code = True
                code_lang = m.group(1)
                code_buffer = []
            else:
                blocks.append(Block("code", "\n".join(code_buffer), {"lang": code_lang}))
                in_code = False
                code_buffer = []
                code_lang = ""
            i += 1
            continue
        if in_code:
            code_buffer.append(raw)
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        # GFM table: current line is `| ... |` and the next is `|---|---|`.
        if _TABLE_ROW.match(line) and i + 1 < len(lines) and _TABLE_ROW.match(lines[i + 1] or ""):
            sep_cells = _split_row(lines[i + 1])
            if sep_cells and all(_TABLE_SEP_CELL.match(c) for c in sep_cells):
                headers = _split_row(line)
                rows: list[list[str]] = []
                j = i + 2
                while j < len(lines) and _TABLE_ROW.match(lines[j] or ""):
                    rows.append(_split_row(lines[j]))
                    j += 1
                blocks.append(
                    Block("table", "", {"headers": headers, "rows": rows})
                )
                i = j
                continue

        if _RULE.match(line.strip()):
            blocks.append(Block("rule"))
            i += 1
            continue
        if m := _H4.match(line):
            blocks.append(Block("h4", m.group(1).strip()))
        elif m := _H3.match(line):
            blocks.append(Block("h3", m.group(1).strip()))
        elif m := _H2.match(line):
            blocks.append(Block("h2", m.group(1).strip()))
        elif m := _H1.match(line):
            blocks.append(Block("h1", m.group(1).strip()))
        elif m := _BLOCKQUOTE.match(line):
            blocks.append(Block("quote", m.group(1).strip()))
        elif m := _BULLET.match(line):
            blocks.append(Block("bullet", m.group(1).strip()))
        elif m := _NUMBERED.match(line):
            blocks.append(Block("numbered", f"{m.group(1)}. {m.group(2).strip()}"))
        else:
            blocks.append(Block("body", line.strip()))
        i += 1

    # Unclosed fence: don't lose the content.
    if in_code and code_buffer:
        blocks.append(Block("code", "\n".join(code_buffer), {"lang": code_lang}))
    return blocks


def _apply_runs(paragraph, text: str) -> None:
    """Walk `text` splitting on inline **bold**, *italic* and `code` marks and
    add each fragment as its own run so the styling actually shows up in Word.

    Without this the paragraph gets the raw asterisks and backticks inline.
    """
    from docx.shared import RGBColor
    # Precedence: bold > italic > code, but the same span can be several at
    # once (rare in practice for our planner). We do one pass per tag.
    pattern = re.compile(
        r"(\*\*[^*\n]+\*\*|(?<!\*)\*[^*\n]+\*(?!\*)|`[^`\n]+`)"
    )
    parts = pattern.split(text)
    for part in parts:
        if not part:
            continue
        run = paragraph.add_run()
        if part.startswith("**") and part.endswith("**"):
            run.text = part[2:-2]
            run.bold = True
        elif part.startswith("*") and part.endswith("*"):
            run.text = part[1:-1]
            run.italic = True
        elif part.startswith("`") and part.endswith("`"):
            run.text = part[1:-1]
            run.font.name = "Consolas"
            run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)
        else:
            run.text = part


def to_docx(markdown: str, title: str) -> bytes:
    """Render Markdown -> DOCX with real headings, tables and inline styling.

    The previous implementation printed body paragraphs for every block, so
    tables came out as `| col | col |` text and bold looked like `**word**`.
    """
    import docx
    from docx.enum.table import WD_ALIGN_VERTICAL
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    document = docx.Document()

    blocks = parse(markdown)
    has_h1 = any(b.kind == "h1" for b in blocks[:2])
    if not has_h1:
        document.add_heading(title, level=0)

    for block in blocks:
        if block.kind == "h1":
            document.add_heading(block.text, level=0)
        elif block.kind == "h2":
            document.add_heading(block.text, level=1)
        elif block.kind == "h3":
            document.add_heading(block.text, level=2)
        elif block.kind == "h4":
            document.add_heading(block.text, level=3)
        elif block.kind == "bullet":
            para = document.add_paragraph(style="List Bullet")
            _apply_runs(para, block.text)
        elif block.kind == "numbered":
            para = document.add_paragraph(style="List Number")
            _apply_runs(para, re.sub(r"^\d+\.\s*", "", block.text))
        elif block.kind == "rule":
            document.add_paragraph("_" * 50)
        elif block.kind == "quote":
            para = document.add_paragraph(style="Intense Quote")
            _apply_runs(para, block.text)
        elif block.kind == "code":
            para = document.add_paragraph()
            run = para.add_run(block.text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)
        elif block.kind == "table":
            headers = (block.extra or {}).get("headers", [])
            rows = (block.extra or {}).get("rows", [])
            if not headers:
                continue
            table = document.add_table(rows=1 + len(rows), cols=len(headers))
            table.style = "Light Grid Accent 1"
            # Header row.
            for j, h in enumerate(headers):
                cell = table.rows[0].cells[j]
                cell.paragraphs[0].text = ""
                _apply_runs(cell.paragraphs[0], h)
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            # Body rows -- pad short rows so Word doesn't complain about mismatch.
            for i, row in enumerate(rows, start=1):
                cells = table.rows[i].cells
                for j in range(len(headers)):
                    cell = cells[j]
                    cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
                    text = row[j] if j < len(row) else ""
                    cell.paragraphs[0].text = ""
                    _apply_runs(cell.paragraphs[0], text)
            document.add_paragraph()  # spacer
        else:
            para = document.add_paragraph()
            para.paragraph_format.space_after = Pt(6)
            _apply_runs(para, block.text)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _inline_html(text: str) -> str:
    """Convert markdown inline marks to reportlab Paragraph mini-HTML.

    Escape first so any raw HTML in the source is neutralised, then reapply
    bold/italic/code as the small tag subset reportlab accepts.
    """
    from xml.sax.saxutils import escape
    safe = escape(text)
    safe = _CODE_INLINE.sub(
        lambda m: f'<font face="Courier" backColor="#F1F5F9">{escape(m.group(1))}</font>',
        safe,
    )
    safe = _BOLD.sub(r"<b>\1</b>", safe)
    safe = _ITALIC.sub(r"<i>\1</i>", safe)
    return safe


def to_pdf(markdown: str, title: str) -> bytes:
    """Render Markdown -> paginated A4 PDF with real tables and inline styling.

    Previously produced a nearly-plain PDF because the parser did not know
    about GFM tables, H1 or inline formatting -- the model wrote a
    perfectly-structured document and every block came out as a body paragraph.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        ListFlowable,
        ListItem,
        Paragraph,
        Preformatted,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=title,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    sheet = getSampleStyleSheet()
    ink = HexColor("#111827")
    muted = HexColor("#4B5563")
    accent = HexColor("#065F46")   # emerald 800, matches the app palette
    stripe = HexColor("#F1F5F9")   # zebra rows

    body = ParagraphStyle(
        "Body", parent=sheet["Normal"], fontSize=10.5, leading=15,
        textColor=ink, alignment=TA_LEFT, spaceAfter=6,
    )
    body_dense = ParagraphStyle(
        "BodyDense", parent=body, fontSize=10, leading=13, spaceAfter=0,
    )
    h1_style = ParagraphStyle(
        "H1", parent=sheet["Heading1"], fontSize=20, leading=24,
        textColor=ink, spaceBefore=0, spaceAfter=10,
    )
    h2_style = ParagraphStyle(
        "H2", parent=sheet["Heading2"], fontSize=14, leading=18,
        textColor=accent, spaceBefore=12, spaceAfter=6,
    )
    h3_style = ParagraphStyle(
        "H3", parent=sheet["Heading3"], fontSize=12, leading=16,
        textColor=ink, spaceBefore=9, spaceAfter=4,
    )
    h4_style = ParagraphStyle(
        "H4", parent=sheet["Heading4"], fontSize=10.5, leading=14,
        textColor=muted, spaceBefore=8, spaceAfter=3,
    )
    quote_style = ParagraphStyle(
        "Quote", parent=body, leftIndent=12, textColor=muted,
        borderColor=accent, borderWidth=0, spaceAfter=8, fontName="Helvetica-Oblique",
    )
    header_cell = ParagraphStyle(
        "TH", parent=body_dense, textColor=HexColor("#ffffff"), fontName="Helvetica-Bold",
    )

    story: list = []

    # Skip an auto-title if the document already opens with an H1 that
    # matches, so we don't get two titles stacked.
    blocks = parse(markdown)
    has_h1 = any(b.kind == "h1" for b in blocks[:2])
    if not has_h1:
        story.append(Paragraph(_inline_html(title), h1_style))
        story.append(Spacer(1, 4))

    bullet_buf: list = []
    number_buf: list = []

    def flush_bullets() -> None:
        if bullet_buf:
            story.append(ListFlowable(
                bullet_buf[:], bulletType="bullet", leftIndent=14,
                bulletFontSize=7, spaceBefore=2, spaceAfter=6,
            ))
            bullet_buf.clear()

    def flush_numbers() -> None:
        if number_buf:
            story.append(ListFlowable(
                number_buf[:], bulletType="1", leftIndent=18,
                bulletFontSize=9, spaceBefore=2, spaceAfter=6,
            ))
            number_buf.clear()

    def flush_lists() -> None:
        flush_bullets()
        flush_numbers()

    for block in blocks:
        if block.kind == "bullet":
            flush_numbers()
            bullet_buf.append(ListItem(Paragraph(_inline_html(block.text), body_dense)))
            continue
        if block.kind == "numbered":
            flush_bullets()
            # Strip the leading "1. " -- ListFlowable numbers the items itself.
            text = re.sub(r"^\d+\.\s*", "", block.text)
            number_buf.append(ListItem(Paragraph(_inline_html(text), body_dense)))
            continue

        flush_lists()

        if block.kind == "h1":
            story.append(Paragraph(_inline_html(block.text), h1_style))
        elif block.kind == "h2":
            story.append(Paragraph(_inline_html(block.text), h2_style))
        elif block.kind == "h3":
            story.append(Paragraph(_inline_html(block.text), h3_style))
        elif block.kind == "h4":
            story.append(Paragraph(_inline_html(block.text), h4_style))
        elif block.kind == "rule":
            story.append(Spacer(1, 6))
            story.append(HRFlowable(width="100%", thickness=0.6, color=HexColor("#CBD5E1")))
            story.append(Spacer(1, 6))
        elif block.kind == "quote":
            story.append(Paragraph(_inline_html(block.text), quote_style))
        elif block.kind == "code":
            code_style = ParagraphStyle(
                "Code", parent=sheet["Code"], fontSize=9, leading=12,
                textColor=ink, backColor=stripe, borderColor=HexColor("#E2E8F0"),
                borderWidth=0.5, borderPadding=6, spaceBefore=6, spaceAfter=10,
                leftIndent=0, rightIndent=0,
            )
            story.append(Preformatted(block.text, code_style))
        elif block.kind == "table":
            headers = block.extra.get("headers", []) if block.extra else []
            rows = block.extra.get("rows", []) if block.extra else []
            data = [
                [Paragraph(_inline_html(h), header_cell) for h in headers],
                *[
                    [Paragraph(_inline_html(c), body_dense) for c in row]
                    for row in rows
                ],
            ]
            avail_w = A4[0] - 40 * mm
            col_w = avail_w / max(len(headers), 1)
            table = Table(
                data, colWidths=[col_w] * len(headers), repeatRows=1,
                hAlign="LEFT",
            )
            style = [
                ("BACKGROUND", (0, 0), (-1, 0), accent),
                ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
                ("TOPPADDING", (0, 1), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, HexColor("#CBD5E1")),
            ]
            # Zebra stripes on body rows so grading tables scan cleanly.
            for r in range(1, len(data)):
                if r % 2 == 0:
                    style.append(("BACKGROUND", (0, r), (-1, r), stripe))
            table.setStyle(TableStyle(style))
            # Wrap in KeepTogether so a small table does not orphan its header
            # on the previous page.
            story.append(Spacer(1, 4))
            story.append(KeepTogether(table))
            story.append(Spacer(1, 8))
        else:
            story.append(Paragraph(_inline_html(block.text), body))

    flush_lists()

    def _footer(canvas, doc) -> None:
        """Page number + document title in the footer."""
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(muted)
        canvas.drawString(20 * mm, 10 * mm, title[:80])
        canvas.drawRightString(
            A4[0] - 20 * mm, 10 * mm, f"Page {doc.page}"
        )
        canvas.restoreState()

    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def to_pptx(slides: list[dict], title: str, subtitle: str = "") -> bytes:
    """Build a deck from [{title, bullets: [...], notes: str}, ...]."""
    from pptx import Presentation
    from pptx.util import Pt

    deck = Presentation()

    cover = deck.slides.add_slide(deck.slide_layouts[0])
    cover.shapes.title.text = title
    if len(cover.placeholders) > 1:
        cover.placeholders[1].text = subtitle

    for slide_spec in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = str(slide_spec.get("title", ""))[:120]

        frame = slide.placeholders[1].text_frame
        frame.clear()
        bullets = [str(b) for b in slide_spec.get("bullets", []) if str(b).strip()]
        for i, bullet in enumerate(bullets):
            # text_frame always starts with one empty paragraph; fill it before
            # adding more, otherwise every deck opens with a blank first line.
            para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
            para.text = bullet
            para.level = 0
            para.font.size = Pt(18)

        notes = str(slide_spec.get("notes", "")).strip()
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    buffer = io.BytesIO()
    deck.save(buffer)
    return buffer.getvalue()


def safe_filename(name: str, extension: str) -> str:
    """Make a filename that survives Content-Disposition and every OS."""
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip() or "document"
    return f"{cleaned[:60].replace(' ', '_')}.{extension}"


def to_xlsx(sheets: list[dict], title: str) -> bytes:
    """Build an .xlsx from a list of sheet specs.

    Each sheet: {"name": "Sheet1", "headers": ["A","B"], "rows": [[...], [...]]}
    The header row is bolded; the first row's column widths are set from the
    header text length so the file opens with readable columns.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)  # start with no sheets, add explicitly

    for i, spec in enumerate(sheets or [{"name": "Sheet1", "headers": [], "rows": []}]):
        name = str(spec.get("name") or f"Sheet{i + 1}")[:31].strip() or f"Sheet{i + 1}"
        headers = list(spec.get("headers") or [])
        rows = list(spec.get("rows") or [])

        ws = wb.create_sheet(title=name)

        if headers:
            ws.append([str(h) for h in headers])
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="left")

        for row in rows:
            # Coerce Nones to empty strings so openpyxl does not raise on stray nulls.
            ws.append(["" if v is None else v for v in row])

        # Rough auto-fit: max(len(header), len(any cell in that column), min 8, cap 60).
        for col_idx in range(1, len(headers) + 1 if headers else max((len(r) for r in rows), default=1) + 1):
            longest = 8
            if headers and col_idx - 1 < len(headers):
                longest = max(longest, len(str(headers[col_idx - 1])))
            for row in rows:
                if col_idx - 1 < len(row):
                    longest = max(longest, len(str(row[col_idx - 1])))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(longest + 2, 60)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
