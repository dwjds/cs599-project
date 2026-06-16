from __future__ import annotations

import html
import re
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "report-source.md"
OUTPUT = ROOT / "CS599_大作业报告.pdf"
FONT = "STSong-Light"


def clean_inline(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    return html.escape(text.strip())


class ReportTemplate(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=22 * mm,
            rightMargin=22 * mm,
            topMargin=20 * mm,
            bottomMargin=18 * mm,
            title="CS599 大作业报告",
            author="MiniAgent Project",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates(PageTemplate(id="report", frames=frame, onPage=self.draw_page))
        self._bookmark_id = 0

    def beforeDocument(self):
        self._bookmark_id = 0

    def draw_page(self, canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        if doc.page > 1:
            canvas.drawCentredString(A4[0] / 2, 10 * mm, f"第 {doc.page - 1} 页")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        levels = {"Heading1": 0, "Heading2": 1, "Heading3": 2}
        level = levels.get(flowable.style.name)
        if level is None:
            return
        text = flowable.getPlainText()
        key = f"heading-{self._bookmark_id}"
        self._bookmark_id += 1
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
        self.notify("TOCEntry", (level, text, self.page - 1, key))


def make_styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName=FONT, textColor=colors.HexColor("#17212B"))
    return {
        "CoverTitle": ParagraphStyle(
            "CoverTitle", **base, fontSize=24, leading=34, alignment=TA_CENTER, spaceAfter=18 * mm
        ),
        "Draft": ParagraphStyle(
            "Draft", fontName=FONT, fontSize=10, leading=16, alignment=TA_CENTER,
            textColor=colors.HexColor("#B54708"),
        ),
        "Heading1": ParagraphStyle(
            "Heading1", **base, fontSize=18, leading=26, spaceBefore=12, spaceAfter=10, keepWithNext=True
        ),
        "Heading2": ParagraphStyle(
            "Heading2", **base, fontSize=14, leading=21, spaceBefore=10, spaceAfter=7, keepWithNext=True
        ),
        "Heading3": ParagraphStyle(
            "Heading3", **base, fontSize=12, leading=18, spaceBefore=8, spaceAfter=5, keepWithNext=True
        ),
        "Body": ParagraphStyle(
            "Body", **base, fontSize=10.5, leading=18, alignment=TA_JUSTIFY, spaceAfter=7, wordWrap="CJK"
        ),
        "Bullet": ParagraphStyle(
            "Bullet", **base, fontSize=10.5, leading=18, leftIndent=13, firstLineIndent=-9, spaceAfter=4, wordWrap="CJK"
        ),
        "Quote": ParagraphStyle(
            "Quote", **base, fontSize=10, leading=17, leftIndent=12, rightIndent=8,
            borderColor=colors.HexColor("#98A2B3"), borderWidth=1, borderPadding=7,
            backColor=colors.HexColor("#F2F4F7"), spaceAfter=8,
        ),
        "Code": ParagraphStyle(
            "Code", fontName=FONT, fontSize=8.5, leading=13, textColor=colors.HexColor("#F9FAFB"),
            backColor=colors.HexColor("#1D2939"), borderPadding=8, leftIndent=4, rightIndent=4, spaceAfter=8,
        ),
        "TOCTitle": ParagraphStyle(
            "TOCTitle", **base, fontSize=20, leading=28, alignment=TA_CENTER, spaceAfter=15 * mm
        ),
    }


def parse_cover(lines: list[str]) -> tuple[str, list[list[str]], int]:
    title = clean_inline(lines[0].lstrip("# "))
    rows: list[list[str]] = []
    body_start = 0
    for index, line in enumerate(lines):
        if line.strip() == r"\newpage":
            body_start = index + 1
            break
        if line.startswith("|") and "---" not in line:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and cells[0] != "字段":
                rows.append(cells)
    return title, rows, body_start


def table_flowable(rows: list[list[str]], styles: dict[str, ParagraphStyle], header: bool = True) -> Table:
    width = 166 * mm
    columns = max(len(row) for row in rows)
    data = []
    for row in rows:
        padded = row + [""] * (columns - len(row))
        data.append([Paragraph(clean_inline(cell), styles["Body"]) for cell in padded])
    table = Table(data, colWidths=[width / columns] * columns, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LEADING", (0, 0), (-1, -1), 14),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D0D5DD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7F0F4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#123A4A")),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def parse_body(lines: list[str], styles: dict[str, ParagraphStyle]) -> list:
    story: list = []
    paragraph: list[str] = []
    code: list[str] = []
    table_rows: list[list[str]] = []
    in_code = False

    def flush_paragraph():
        if paragraph:
            story.append(Paragraph(clean_inline(" ".join(paragraph)), styles["Body"]))
            paragraph.clear()

    def flush_table():
        if table_rows:
            story.append(table_flowable(table_rows.copy(), styles))
            story.append(Spacer(1, 5))
            table_rows.clear()

    for raw_line in lines + [""]:
        line = raw_line.rstrip()
        if line.startswith("```"):
            flush_paragraph()
            flush_table()
            if in_code:
                story.append(Preformatted("\n".join(code), styles["Code"]))
                code.clear()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        if line.startswith("|"):
            flush_paragraph()
            if "---" not in line:
                table_rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
            continue
        flush_table()
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            story.append(Paragraph(clean_inline(heading.group(2)), styles[f"Heading{level}"]))
            continue
        if line.startswith("- "):
            flush_paragraph()
            story.append(Paragraph(f"• {clean_inline(line[2:])}", styles["Bullet"]))
            continue
        if line.startswith("> "):
            flush_paragraph()
            story.append(Paragraph(clean_inline(line[2:]), styles["Quote"]))
            continue
        if not line.strip():
            flush_paragraph()
            continue
        paragraph.append(line.strip())
    return story


def count_outline(items) -> int:
    return sum(count_outline(item) if isinstance(item, list) else 1 for item in items)


def build_report() -> None:
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    styles = make_styles()
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    title, cover_rows, body_start = parse_cover(lines)
    cover_table = table_flowable(cover_rows, styles, header=False)
    cover_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E7F0F4")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#123A4A")),
            ]
        )
    )

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC1", fontName=FONT, fontSize=11, leading=18, leftIndent=0, spaceBefore=4),
        ParagraphStyle("TOC2", fontName=FONT, fontSize=10, leading=16, leftIndent=16),
        ParagraphStyle("TOC3", fontName=FONT, fontSize=9, leading=14, leftIndent=30),
    ]
    story = [
        Spacer(1, 25 * mm),
        Paragraph(title, styles["CoverTitle"]),
        KeepTogether(cover_table),
        Spacer(1, 12 * mm),
        Paragraph("草稿提示：个人信息、课程总结与截图仍需在提交前补充。", styles["Draft"]),
        PageBreak(),
        Paragraph("目录", styles["TOCTitle"]),
        toc,
        PageBreak(),
    ]
    story.extend(parse_body(lines[body_start:], styles))

    doc = ReportTemplate(str(OUTPUT))
    doc.multiBuild(story)
    reader = PdfReader(str(OUTPUT))
    print(f"Generated: {OUTPUT}")
    print(f"Pages: {len(reader.pages)}")
    print(f"Bookmarks: {count_outline(reader.outline)}")


if __name__ == "__main__":
    build_report()
