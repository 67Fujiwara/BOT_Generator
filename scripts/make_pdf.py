#!/usr/bin/env python3
"""Markdown / テキストから日本語 PDF を作る（全ボット共通）。

  python scripts/make_pdf.py <input.md|.txt> [--out <x.pdf>] [--title "見出し"] [--font <ttf/ttc>] [--landscape]

日本語フォントは次の順で探して埋め込む（サブセット）:
  --font / 環境変数 BOT_PDF_FONT / samples/_fonts/*.ttf|ttc / Windows（メイリオ・游ゴシック・BIZ UD・MS ゴシック）
  / macOS（ヒラギノ）/ Linux（Noto Sans CJK・IPA ゴシック）。
どれも無ければ Adobe-Japan1 の CID フォント（HeiseiKakuGo-W5。埋め込まず、閲覧ソフトの日本語フォントで表示）にする。
対応する Markdown: #〜#### 見出し / - * 箇条書き / 1. 番号 / > 引用 / ``` コード / | 表 | / --- 区切り /
**太字** *斜体* `コード` [リンク](url) ![画像](パス。/files/… も可)。
出力は標準出力に「PDF: <パス>」と、チャットに貼れるリンク行。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_CANDIDATES = [
    # Windows
    (r"C:\Windows\Fonts\meiryo.ttc", 0), (r"C:\Windows\Fonts\YuGothM.ttc", 0), (r"C:\Windows\Fonts\BIZ-UDGothicR.ttc", 0),
    (r"C:\Windows\Fonts\msgothic.ttc", 0), (r"C:\Windows\Fonts\YuGothR.ttc", 0),
    # macOS
    ("/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc", 0), ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    # Linux
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0), ("/usr/share/fonts/truetype/noto/NotoSansCJKjp-Regular.otf", 0),
    ("/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf", 0), ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", 0),
    ("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", 0), ("/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf", 0),
]


def register_font(explicit: str | None) -> tuple[str, str]:
    """(フォント名, どう決めたか) を返す。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    cands: list[tuple[str, int]] = []
    if explicit:
        cands.append((explicit, 0))
    if os.environ.get("BOT_PDF_FONT"):
        cands.append((os.environ["BOT_PDF_FONT"], 0))
    fdir = ROOT / "samples" / "_fonts"
    if fdir.is_dir():
        cands += [(str(p), 0) for p in sorted(fdir.iterdir()) if p.suffix.lower() in (".ttf", ".ttc", ".otf")]
    cands += FONT_CANDIDATES
    for path, idx in cands:
        if not Path(path).exists():
            continue
        try:
            if path.lower().endswith(".ttc"):
                pdfmetrics.registerFont(TTFont("JP", path, subfontIndex=idx))
            else:
                pdfmetrics.registerFont(TTFont("JP", path))
            return "JP", f"埋め込み: {Path(path).name}"
        except Exception:  # noqa: BLE001
            continue
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    return "HeiseiKakuGo-W5", "Adobe-Japan1 CID フォント（HeiseiKakuGo-W5、埋め込みなし）"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(s: str) -> str:
    """Markdown のインライン → reportlab の mini-HTML。"""
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r'<font backColor="#eeeeee">\1</font>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" color="#1a5fb4">\1</a>', s)
    return s


def resolve_img(src: str, base: Path) -> Path | None:
    src = src.strip()
    if src.startswith("/files/"):
        p = ROOT / src[len("/files/"):]
    else:
        p = Path(src)
        if not p.is_absolute():
            p = (base / p) if (base / p).exists() else (ROOT / p)
    return p if p.exists() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif") else None


def build(md: str, out: Path, title: str | None, font: str, landscape_: bool, base: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (HRFlowable, Image, ListFlowable, ListItem, PageBreak, Paragraph, Preformatted,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    size = landscape(A4) if landscape_ else A4
    doc = SimpleDocTemplate(str(out), pagesize=size, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=title or out.stem, author="Bots")
    body = ParagraphStyle("body", fontName=font, fontSize=10.5, leading=16, alignment=TA_LEFT, spaceAfter=4)
    styles = {
        1: ParagraphStyle("h1", parent=body, fontSize=18, leading=24, spaceBefore=6, spaceAfter=8),
        2: ParagraphStyle("h2", parent=body, fontSize=14.5, leading=20, spaceBefore=10, spaceAfter=5),
        3: ParagraphStyle("h3", parent=body, fontSize=12.5, leading=18, spaceBefore=8, spaceAfter=4),
        4: ParagraphStyle("h4", parent=body, fontSize=11, leading=16, spaceBefore=6, spaceAfter=3),
    }
    quote = ParagraphStyle("quote", parent=body, leftIndent=10, textColor=colors.HexColor("#555555"), borderPadding=2)
    code = ParagraphStyle("code", parent=body, fontSize=9, leading=12.5, backColor=colors.HexColor("#f3f3f3"), leftIndent=4, rightIndent=4,
                          borderPadding=4, spaceBefore=4, spaceAfter=6)
    cell = ParagraphStyle("cell", parent=body, fontSize=9.5, leading=13, spaceAfter=0)
    avail_w = size[0] - doc.leftMargin - doc.rightMargin

    story = []
    if title:
        story.append(Paragraph(inline(title), styles[1]))
    lines = md.splitlines()
    i, para, bullets, numbers, table = 0, [], [], [], []

    def flush_para():
        nonlocal para
        if para:
            story.append(Paragraph(inline(" ".join(para)), body)); para = []

    def flush_lists():
        nonlocal bullets, numbers
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(inline(b), body), leftIndent=12) for b in bullets], bulletType="bullet", start="・",
                                      bulletFontName=font, leftIndent=12)); bullets = []
        if numbers:
            story.append(ListFlowable([ListItem(Paragraph(inline(b), body), leftIndent=14) for b in numbers], bulletType="1", bulletFontName=font,
                                      leftIndent=14)); numbers = []

    def flush_table():
        nonlocal table
        if not table:
            return
        rows = [[Paragraph(inline(c.strip()), cell) for c in r] for r in table]
        ncol = max(len(r) for r in rows)
        rows = [r + [Paragraph("", cell)] * (ncol - len(r)) for r in rows]
        t = Table(rows, colWidths=[avail_w / ncol] * ncol, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef5")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(t); story.append(Spacer(1, 6)); table = []

    def flush_all():
        flush_para(); flush_lists(); flush_table()

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            flush_all(); buf = []; i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            story.append(Preformatted("\n".join(buf), code, maxLineLength=110)); i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush_all(); story.append(Paragraph(inline(m.group(2)), styles[len(m.group(1))])); i += 1; continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush_all(); story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#999999"), spaceBefore=4, spaceAfter=6)); i += 1; continue
        if s == "<<<改ページ>>>" or s.lower() == "<!-- pagebreak -->":
            flush_all(); story.append(PageBreak()); i += 1; continue
        mi = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", s)
        if mi:
            flush_all(); p = resolve_img(mi.group(2), base)
            if p:
                try:
                    from reportlab.lib.utils import ImageReader
                    iw, ih = ImageReader(str(p)).getSize()
                    w = min(avail_w, iw * 0.75); h = w * ih / iw
                    if h > 160 * mm:
                        h = 160 * mm; w = h * iw / ih
                    story.append(Image(str(p), width=w, height=h)); 
                    if mi.group(1):
                        story.append(Paragraph(inline(mi.group(1)), ParagraphStyle("cap", parent=body, fontSize=9, textColor=colors.HexColor("#555555"))))
                except Exception:  # noqa: BLE001
                    story.append(Paragraph(inline(f"（画像を読めない: {mi.group(2)}）"), body))
            else:
                story.append(Paragraph(inline(f"（画像が見つからない: {mi.group(2)}）"), body))
            i += 1; continue
        if s.startswith("|") and s.endswith("|"):
            flush_para(); flush_lists()
            cells = [c for c in s.strip("|").split("|")]
            if not all(re.fullmatch(r"\s*:?-{2,}:?\s*", c) for c in cells):
                table.append(cells)
            i += 1; continue
        else:
            flush_table()
        mb = re.match(r"^[-*+]\s+(.*)$", s)
        mn = re.match(r"^\d+[.)]\s+(.*)$", s)
        if mb:
            flush_para(); numbers and flush_lists(); bullets.append(mb.group(1)); i += 1; continue
        if mn:
            flush_para(); bullets and flush_lists(); numbers.append(mn.group(1)); i += 1; continue
        if s.startswith(">"):
            flush_all(); story.append(Paragraph(inline(s.lstrip("> ").strip()), quote)); i += 1; continue
        if not s:
            flush_all(); i += 1; continue
        flush_lists(); para.append(s); i += 1
    flush_all()

    def footer(canvas, d):
        canvas.saveState(); canvas.setFont(font, 8.5); canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawRightString(size[0] - 18 * mm, 10 * mm, f"{d.page}")
        if title:
            canvas.drawString(18 * mm, 10 * mm, title[:60])
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Markdown/テキスト → 日本語 PDF")
    ap.add_argument("input")
    ap.add_argument("--out", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--font", default=None)
    ap.add_argument("--landscape", action="store_true")
    a = ap.parse_args(argv)
    src = Path(a.input)
    if not src.exists():
        print(f"見つからない: {a.input}"); return 2
    md = src.read_text(encoding="utf-8", errors="replace")
    out = Path(a.out) if a.out else src.with_suffix(".pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    font, how = register_font(a.font)
    build(md, out, a.title, font, a.landscape, src.parent)
    try:
        rel = out.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = str(out)
    print(f"PDF: {rel}（フォント {how}）")
    if rel.startswith(("inbox/", "samples/")):
        print(f"リンク: [{out.name}](/files/{rel})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
